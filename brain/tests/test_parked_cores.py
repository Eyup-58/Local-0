"""per_core_percent's nullable entries, on both boundaries.

The contract was widened on 2026-08-11 so a parked core can keep its slot rather than costing the
whole array. These tests hold the two halves of that decision in place: nulls are accepted, and the
range check on real values survived the widening.

See docs/CONTRACTS.md sections 3 and 5.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from conftest import ipc_hello, ipc_telemetry_sample
from local_zero_brain.contracts.ipc import IPC_MESSAGE_ADAPTER
from local_zero_brain.ipc.pipe_client import PipeConnected, PipeLine
from local_zero_brain.ipc.session import Accepted, SystemSession
from local_zero_brain.metrics import DropCounters
from test_link import Recorder, build, feed


def sample_with_cores(per_core: list[float | None]) -> dict:
    sample = ipc_telemetry_sample()
    sample["payload"]["cpu"]["per_core_percent"] = per_core
    return sample


def test_a_parked_core_is_accepted_as_null() -> None:
    message = sample_with_cores([12.4, None, 22.7, None])

    parsed = IPC_MESSAGE_ADAPTER.validate_python(message)

    assert parsed.payload.cpu.per_core_percent == [12.4, None, 22.7, None]


def test_an_entirely_absent_array_is_still_accepted() -> None:
    """The whole array goes null when the instance set itself was incomplete, which makes the
    position-to-core mapping unknowable."""
    message = sample_with_cores(None)  # type: ignore[arg-type]

    parsed = IPC_MESSAGE_ADAPTER.validate_python(message)

    assert parsed.payload.cpu.per_core_percent is None


def test_an_all_null_array_is_accepted() -> None:
    """Every core parked at once is unusual but not impossible, and it is not a contract error."""
    message = sample_with_cores([None, None, None, None])

    parsed = IPC_MESSAGE_ADAPTER.validate_python(message)

    assert parsed.payload.cpu.per_core_percent == [None, None, None, None]


@pytest.mark.parametrize("bad_value", [140.0, -1.0])
def test_widening_did_not_loosen_the_range_check(bad_value: float) -> None:
    """The entries that do carry a value are still percentages. A schema edit that replaced the
    item type outright rather than widening it would pass every other test here."""
    message = sample_with_cores([12.4, None, bad_value])

    with pytest.raises(ValidationError) as caught:
        IPC_MESSAGE_ADAPTER.validate_python(message)

    assert "per_core_percent" in str(caught.value)


def test_a_string_entry_is_still_refused() -> None:
    message = sample_with_cores([12.4, "idle"])  # type: ignore[list-item]

    with pytest.raises(ValidationError):
        IPC_MESSAGE_ADAPTER.validate_python(message)


def test_the_session_accepts_a_sample_with_parked_cores() -> None:
    counters = DropCounters()
    session = SystemSession(counters)
    session.handle(json.dumps(ipc_hello()))

    result = session.handle(json.dumps(sample_with_cores([12.4, None, 22.7])))

    assert isinstance(result, Accepted)
    assert counters.snapshot().total == 0


@pytest.mark.asyncio
async def test_nulls_survive_the_forward_to_the_ui() -> None:
    """The brain forwards the payload unchanged, so a parked core must reach the UI as a null in
    its own slot rather than being dropped or turned into a zero somewhere in the middle."""
    link, queue, recorder, _, _ = build()
    per_core = [12.4, None, 22.7, None, 3.0]

    await feed(
        link,
        queue,
        [
            PipeConnected(),
            PipeLine(json.dumps(ipc_hello())),
            PipeLine(json.dumps(sample_with_cores(per_core))),
        ],
    )

    forwarded = recorder.of_type("telemetry.sample")
    assert len(forwarded) == 1
    assert forwarded[0]["payload"]["cpu"]["per_core_percent"] == per_core
