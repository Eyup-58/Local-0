"""Field types and payload shapes shared by both contracts.

The telemetry payload is currently identical on the IPC and WebSocket boundaries, and the two
schemas still define it separately on purpose: the boundaries version independently, and the brain
is free to reshape what it forwards. These shared Python types are an implementation convenience
for M1, where the brain forwards the payload unchanged. The moment the two shapes diverge, they
stop being shared here and the divergence is written into docs/CONTRACTS.md section 4.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = 1

#: Contract major version. A receiver drops any message whose ``v`` it does not implement.
ProtocolVersion = Literal[1]

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

#: RFC 3339, UTC, millisecond precision, always ending in Z.
#:
#: Kept as a constrained string rather than a ``datetime``. Parsing to a datetime and formatting it
#: back is a round trip that can quietly change precision or offset spelling, and the brain
#: forwards timestamps it did not create.
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"

MAX_ERROR_MESSAGE_LENGTH = 500

MessageId = Annotated[str, Field(pattern=UUID_PATTERN)]
Timestamp = Annotated[str, Field(pattern=TIMESTAMP_PATTERN)]
AppVersion = Annotated[str, Field(pattern=SEMVER_PATTERN)]

#: A percentage in [0, 100], or None when the sensor is unavailable.
#:
#: None means UNAVAILABLE. It is rendered as a labelled gap, never as zero, never interpolated and
#: never inferred from load. See CLAUDE.md invariant 10.
Percent = Annotated[float, Field(ge=0, le=100)] | None

#: One entry of per_core_percent. Nullable in its own right, because a core Windows has parked
#: reports no utilization and still has to keep its slot - position is the core's identity, and
#: compacting the array would draw one core's load on another's bar. Widened from a non-nullable
#: number on 2026-08-11; see docs/CONTRACTS.md section 5.
CorePercent = Annotated[float, Field(ge=0, le=100)] | None

NullableNumber = float | None
NullableInteger = int | None

PollIntervalMs = Annotated[int, Field(ge=250, le=60000)]

SensorSourceName = Literal["pdh_english", "win32_api", "wmi", "adlx", "none"]


class ContractModel(BaseModel):
    """Base for every wire model: unknown fields are a rejection, not a field to ignore."""

    model_config = ConfigDict(extra="forbid")


class SensorCapability(ContractModel):
    """One entry of the sensor declaration.

    This is the honesty mechanism of the contract. Without it the UI sees
    ``cpu.temperature_c: null`` and cannot tell a missing sensor from a transient read failure from
    a bug. With it, the UI renders ``unavailable_reason`` verbatim and the user knows where they
    stand.
    """

    field: Annotated[str, Field(min_length=1)]
    available: bool
    source: SensorSourceName
    unavailable_reason: str | None

    @model_validator(mode="after")
    def _unavailable_sensors_explain_themselves(self) -> SensorCapability:
        """Mirror the schema's conditional: a silent gap is a rejected message.

        Only the ``available is False`` branch is enforced, because that is all the schema
        constrains. A model stricter than the contract would reject messages the contract permits,
        which is its own kind of drift.
        """
        if self.available:
            return self

        if not self.unavailable_reason:
            raise ValueError(
                "an unavailable sensor must carry a non-empty unavailable_reason: "
                "the UI shows it verbatim, and without one a gap is indistinguishable from a bug"
            )
        if self.source != "none":
            raise ValueError("an unavailable sensor must declare source 'none'")

        return self


class CpuPayload(ContractModel):
    total_percent: Percent
    #: Either the whole array is None, or it has one entry per logical processor with None marking
    #: the cores that were parked. It is never compacted.
    per_core_percent: list[CorePercent] | None
    frequency_mhz: NullableNumber
    #: Expected to be None permanently on this machine: no ring-0 driver, so no source exists at
    #: any privilege level. See docs/ARCHITECTURE.md section 3.
    temperature_c: NullableNumber


class MemoryPayload(ContractModel):
    used_bytes: NullableInteger
    total_bytes: NullableInteger
    commit_used_bytes: NullableInteger
    commit_limit_bytes: NullableInteger


class GpuPayload(ContractModel):
    utilization_percent: Percent
    vram_used_bytes: NullableInteger
    vram_total_bytes: NullableInteger
    #: None until the AMD ADLX spike resolves in M5. Nothing may depend on it before then.
    temperature_c: NullableNumber


class TelemetryPayload(ContractModel):
    #: Monotonic from 0 per connection. A gap means samples were dropped, and the consumer can say
    #: so rather than silently presenting a discontinuity as continuous data.
    seq: Annotated[int, Field(ge=0)]
    #: When the machine was read, as distinct from the envelope ``ts``, which is when the message
    #: was built. Under load these differ, and conflating them makes latency unmeasurable.
    sampled_at: Timestamp
    cpu: CpuPayload
    memory: MemoryPayload
    gpu: GpuPayload
    uptime_seconds: NullableInteger
