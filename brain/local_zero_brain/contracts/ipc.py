"""Mirror of contracts/ipc.schema.json - the system (C#) to brain (Python) boundary.

Transport is newline-delimited JSON over a Windows named pipe, UTF-8 without a BOM.
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

IpcErrorCode = Literal[
    "schema_violation",
    "unsupported_version",
    "sensor_read_failed",
    "handshake_required",
    "internal_error",
]


class IpcHelloPayload(ContractModel):
    component: Literal["system"]
    app_version: AppVersion
    #: Declared ``const: false`` by the schema, so ``Literal[False]`` here is not defensiveness -
    #: it is the contract. A sidecar claiming elevation fails validation, and the brain refuses the
    #: connection rather than trusting a component that is already behaving unexpectedly.
    #: See docs/ARCHITECTURE.md section 2.
    elevated: Literal[False]
    poll_interval_ms: PollIntervalMs
    #: Every field the UI can display, exactly once, available or not.
    sensors: Annotated[list[SensorCapability], Field(min_length=1)]


class IpcHello(ContractModel):
    """First message on every new pipe connection.

    The brain must not accept telemetry before a valid hello; an unsolicited telemetry.sample gets
    ``handshake_required``.
    """

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["hello"]
    payload: IpcHelloPayload


class IpcTelemetrySample(ContractModel):
    """One reading. Emitted on the sidecar's own tick; the brain never polls."""

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["telemetry.sample"]
    payload: TelemetryPayload


class IpcErrorPayload(ContractModel):
    code: IpcErrorCode
    #: User-safe prose. Never a secret, a stack trace, or a filesystem path outside the allowed
    #: roots - detail for debugging goes to the log, not onto the wire.
    message: Annotated[str, Field(min_length=1, max_length=500)]
    in_reply_to: MessageId | None


class IpcError(ContractModel):
    """Reports a fault without terminating the connection."""

    v: ProtocolVersion
    id: MessageId
    ts: Timestamp
    type: Literal["error"]
    payload: IpcErrorPayload


IpcMessage = Annotated[
    IpcHello | IpcTelemetrySample | IpcError,
    Field(discriminator="type"),
]

#: Discriminated on ``type`` so a failure names the offending field of the intended message rather
#: than collapsing into "did not match any variant" - the same reason the M0 example validator
#: narrows to a single definition before validating.
IPC_MESSAGE_ADAPTER: TypeAdapter[IpcMessage] = TypeAdapter(IpcMessage)
