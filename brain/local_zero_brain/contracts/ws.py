"""Mirror of contracts/ws.schema.json - the brain (Python) to ui (TypeScript) boundary.

One JSON object per WebSocket text frame. Every string in every message is rendered by the UI as
text: no markdown renderer is imported on this path and dangerouslySetInnerHTML is banned
repository-wide. That is a security control, not a styling choice - see docs/SECURITY.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from local_zero_brain.contracts.common import (
    UUID_PATTERN,
    AppVersion,
    ContractModel,
    MessageId,
    PollIntervalMs,
    ProtocolVersion,
    SensorCapability,
    TelemetryPayload,
    Timestamp,
)

WsErrorCode = Literal[
    "schema_violation",
    "unsupported_version",
    "handshake_required",
    #: Replaces the IPC contract's sensor_read_failed: from the UI's side, an unreadable sensor and
    #: a dead sidecar are both "the system layer cannot tell you right now".
    "system_unavailable",
    #: The selected model layer cannot be used - today, only "cloud was chosen and no key is
    #: stored". Distinct from system_unavailable, which is about the sidecar: a user who confuses
    #: "the sensors are down" with "your key is missing" goes looking in the wrong place.
    "provider_unavailable",
    "internal_error",
]


class ClientHelloPayload(ContractModel):
    component: Literal["ui"]
    app_version: AppVersion


class ClientHello(ContractModel):
    """First frame the UI sends. The brain streams nothing before it."""

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["client.hello"]
    payload: ClientHelloPayload


class ServerHelloPayload(ContractModel):
    component: Literal["brain"]
    app_version: AppVersion
    poll_interval_ms: PollIntervalMs
    #: Whether the brain currently holds a live pipe to the system layer. When false the UI shows a
    #: disconnected state; it never shows stale numbers as if they were live.
    system_connected: bool
    #: An empty list is legal and means the system layer has not handshaked yet. The UI shows a
    #: starting-up state, not an empty panel that looks like zero sensors exist.
    sensors: list[SensorCapability]


class ServerHello(ContractModel):
    """Sent once in reply to client.hello, before any telemetry."""

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["server.hello"]
    payload: ServerHelloPayload


class SystemStatusPayload(ContractModel):
    connected: bool
    since: Timestamp
    #: User-safe prose explaining a disconnect, or None while connected.
    reason: Annotated[str, Field(max_length=500)] | None
    sensors: list[SensorCapability]


class SystemStatus(ContractModel):
    """Emitted whenever the system layer connects or drops.

    This message is why the UI can degrade honestly. Without it a dead sidecar looks identical to
    an idle machine - the numbers simply stop changing. With it, the UI marks the data stale and
    stops presenting the last sample as live. An M1 exit criterion, not a nicety.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["system.status"]
    payload: SystemStatusPayload


class WsTelemetrySample(ContractModel):
    """Forwarded from the system layer.

    In M1 the payload is forwarded unchanged and only the envelope is re-stamped. The first time
    that diverges, the divergence is written into docs/CONTRACTS.md section 4 - it is never allowed
    to become an undocumented difference between two files that look the same.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["telemetry.sample"]
    payload: TelemetryPayload


class WsErrorPayload(ContractModel):
    code: WsErrorCode
    message: Annotated[str, Field(min_length=1, max_length=500)]
    in_reply_to: MessageId | None


class WsError(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["error"]
    payload: WsErrorPayload


RequestId = Annotated[str, Field(pattern=UUID_PATTERN)]

SideEffectName = Literal["read", "write", "destructive"]
OriginName = Literal["user_direct", "untrusted_content"]
ApprovalOutcome = Literal["approved", "rejected", "expired", "auto_approved"]

#: One resolved argument as it appears in an approval payload.
#:
#: Scalars only. A nested structure is a rendering decision waiting to be got wrong, and rendering
#: decisions are exactly where markup re-enters a payload the user is reading in order to decide.
#: The schema holds the same line; rejected/ws.approval-nested-args.json proves it.
ResolvedArgument = Annotated[str, Field(max_length=65536)] | float | int | bool | None


class ApprovalRequestPayload(ContractModel):
    """Built by the brain from an invocation that already passed steps 1-3 of the guard.

    No part of this comes from a model. ``resolved_args`` is what will actually run - post
    validation, post canonicalisation - rather than what was asked for, and ``affected_paths`` is
    computed rather than narrated. See docs/SECURITY.md section 5.
    """

    request_id: RequestId
    capability: Annotated[str, Field(min_length=1)]
    resolved_args: dict[str, ResolvedArgument]
    affected_paths: list[Annotated[str, Field(min_length=1)]]
    side_effect: SideEffectName
    origin: OriginName


class ApprovalRequest(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["approval.request"]
    payload: ApprovalRequestPayload


class ApprovalDecisionPayload(ContractModel):
    request_id: RequestId
    #: A closed enum on purpose: a decision the brain cannot interpret fails closed rather than
    #: being read as either answer.
    decision: Literal["approve", "reject"]


class ApprovalDecision(ContractModel):
    """The entire extent of the UI's authority over an invocation.

    It answers one the brain already resolved. It cannot construct, alter or re-scope one.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["approval.decision"]
    payload: ApprovalDecisionPayload


class ApprovalResolvedPayload(ContractModel):
    request_id: RequestId
    outcome: ApprovalOutcome


class ApprovalResolved(ContractModel):
    """Closes a request out so no dialog lingers over something already settled.

    ``auto_approved`` is what trust mode produces. It exists so operations the button let through are
    still visible, rather than absent because no dialog was ever raised.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["approval.resolved"]
    payload: ApprovalResolvedPayload


class TrustStatusPayload(ContractModel):
    #: When true, approval is bypassed for every invocation regardless of side_effect or origin. The
    #: name whitelist, the argument schema and path containment continue to apply: trust mode skips
    #: the approval gate, not the guard.
    enabled: bool
    since: Timestamp


class TrustStatus(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["trust.status"]
    payload: TrustStatusPayload


class TrustSetPayload(ContractModel):
    enabled: bool


class TrustSet(ContractModel):
    """The only way trust mode changes, and only the UI may send it.

    It is not a registered capability, so no invocation can reach it, and the state file it writes
    lives outside every capability's allowed_roots. A toggle a model could flip would not be a
    feature.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["trust.set"]
    payload: TrustSetPayload


ProviderModeName = Literal["local", "cloud"]


class ProviderStatusPayload(ContractModel):
    #: ``local`` sends nothing off this machine; ``cloud`` additionally permits outbound. See
    #: docs/SECURITY.md section 11.
    mode: ProviderModeName
    model: Annotated[str, Field(min_length=1)]
    #: Whether a key is stored. Never the key, never a prefix of it, never its length - and there is
    #: a rejected example holding that line, because "just the last four characters" is exactly the
    #: change somebody makes later in good faith.
    has_key: bool
    since: Timestamp


class ProviderStatus(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["provider.status"]
    payload: ProviderStatusPayload


class ProviderSelectPayload(ContractModel):
    mode: ProviderModeName


class ProviderSelect(ContractModel):
    """The user's own switch over the network boundary, not an invocation.

    Like ``TrustSet``: not a registered capability, so nothing a model proposes can reach it.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["provider.select"]
    payload: ProviderSelectPayload


class CredentialSetPayload(ContractModel):
    #: Bounded on both ends. Empty is refused where the mistake is rather than later as an
    #: authentication failure with nothing pointing back here.
    key: Annotated[str, Field(min_length=1, max_length=4096)]


class CredentialSet(ContractModel):
    """The key, crossing loopback once on its way into the Windows Credential Manager.

    **This payload is never logged, never audited, and never echoed in a validation error.** The
    acknowledgement is a ``provider.status`` carrying ``has_key``, not the value. A schema cannot
    express "do not write this down", so the rule lives in docs/CONTRACTS.md section 5 and in the
    brain's handling of this message.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["credential.set"]
    payload: CredentialSetPayload


class MemoryStatusPayload(ContractModel):
    #: False when no vault is configured, or the configured one is not there. An ordinary state:
    #: nothing else in the product depends on memory.
    enabled: bool
    vault: str | None
    notes: Annotated[int, Field(ge=0)]
    chunks: Annotated[int, Field(ge=0)]
    embedded_chunks: Annotated[int, Field(ge=0)]
    last_indexed_at: Timestamp | None
    #: False means keyword-only ranking. Reported rather than inferred - search that quietly gets
    #: worse is the failure nobody notices.
    embeddings_available: bool


class MemoryStatus(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["memory.status"]
    payload: MemoryStatusPayload


class MemoryReindexPayload(ContractModel):
    """Empty, deliberately.

    A vault path arriving from the UI would be a directory to walk, read and index, chosen by the
    least authoritative component on this socket. The vault is the configured one.
    """


class MemoryReindex(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["memory.reindex"]
    payload: MemoryReindexPayload


#: What the brain is doing in the current conversational turn.
#:
#: Reported, never inferred. The UI has no timer that advances this and no default that fills it in,
#: which is the same rule trust and provider mode follow: a panel that decided for itself that the
#: brain was "probably speaking by now" would be narrating, and CLAUDE.md invariant 10 forbids
#: exactly that kind of plausible-looking placeholder.
TurnStateName = Literal["idle", "listening", "thinking", "tool_running", "speaking"]

#: running is not terminal. Nothing may conclude a turn on the strength of it.
ToolLogStatus = Literal["running", "ok", "failed"]


class TurnStatePayload(ContractModel):
    state: TurnStateName
    since: Timestamp
    #: What the brain is saying, in its own words, or None when it has nothing to say. None is a gap
    #: and the UI renders it as one - it does not substitute a greeting or filler. min_length 1 keeps
    #: "" from becoming a second way to spell silence that renders as a blank line instead.
    caption: Annotated[str, Field(min_length=1, max_length=2000)] | None
    #: A short label for what the state is about - the capability running, the device listened on.
    detail: Annotated[str, Field(min_length=1, max_length=120)] | None


class TurnState(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["turn.state"]
    payload: TurnStatePayload


class ToolLogPayload(ContractModel):
    at: Timestamp
    #: The registered capability name as the guard's step 1 knows it, not a name the model chose.
    capability: Annotated[str, Field(min_length=1, max_length=120)]
    #: MAY paraphrase content the brain fetched, which makes it untrusted text by docs/SECURITY.md
    #: section 2. It is safe to display because the UI renders it as text and it never reaches the
    #: planner; nothing that reads it may treat it as an instruction.
    message: Annotated[str, Field(min_length=1, max_length=500)]
    status: ToolLogStatus


class ToolLog(ContractModel):
    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["tool.log"]
    payload: ToolLogPayload


WsMessage = Annotated[
    ClientHello
    | ServerHello
    | SystemStatus
    | WsTelemetrySample
    | WsError
    | ApprovalRequest
    | ApprovalDecision
    | ApprovalResolved
    | TrustStatus
    | TrustSet
    | ProviderStatus
    | ProviderSelect
    | CredentialSet
    | MemoryStatus
    | MemoryReindex
    | TurnState
    | ToolLog,
    Field(discriminator="type"),
]

WS_MESSAGE_ADAPTER: TypeAdapter[WsMessage] = TypeAdapter(WsMessage)

#: Inbound frames are narrowed to what the UI is allowed to send. The UI holds no authority: it
#: cannot construct a capability invocation. Accepting the full union inbound would let a browser tab
#: send the brain a server.hello, or raise its own approval.request and then answer it.
ClientMessage = Annotated[
    ClientHello | ApprovalDecision | TrustSet | ProviderSelect | CredentialSet | MemoryReindex,
    Field(discriminator="type"),
]

CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)

#: The handshake is stricter still: the *first* frame must be a hello and nothing else, so a client
#: cannot open with an approval.decision for a request that predates its connection.
CLIENT_HELLO_ADAPTER: TypeAdapter[ClientHello] = TypeAdapter(ClientHello)
