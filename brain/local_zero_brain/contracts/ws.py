"""Mirror of contracts/ws.schema.json - the brain (Python) to ui (TypeScript) boundary.

One JSON object per WebSocket text frame. Every string in every message is rendered by the UI as
text: no markdown renderer is imported on this path and dangerouslySetInnerHTML is banned
repository-wide. That is a security control, not a styling choice - see docs/SECURITY.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from local_zero_brain.contracts.common import (
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


WsMessage = Annotated[
    ClientHello | ServerHello | SystemStatus | WsTelemetrySample | WsError,
    Field(discriminator="type"),
]

WS_MESSAGE_ADAPTER: TypeAdapter[WsMessage] = TypeAdapter(WsMessage)

#: Inbound frames are narrowed to what the UI is allowed to send. The UI holds no authority: it
#: cannot construct a capability invocation, and in M1 the only frame it may send at all is its
#: hello. Accepting the full union inbound would let a browser tab send the brain a server.hello.
CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientHello] = TypeAdapter(ClientHello)
