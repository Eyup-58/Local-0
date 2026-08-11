"""The handshake gate and the drop-and-count rules on the system boundary."""

from __future__ import annotations

import json

from conftest import ipc_hello, ipc_telemetry_sample
from local_zero_brain.contracts.ipc import IpcHello, IpcTelemetrySample
from local_zero_brain.ipc.session import (
    MAX_VIOLATIONS_PER_CONNECTION,
    Accepted,
    Rejected,
    SystemSession,
)
from local_zero_brain.metrics import DropCounters


def make_session() -> tuple[SystemSession, DropCounters]:
    counters = DropCounters()
    return SystemSession(counters), counters


def line(message: dict) -> str:
    return json.dumps(message)


def test_a_valid_hello_establishes_the_session() -> None:
    session, _ = make_session()

    result = session.handle(line(ipc_hello()))

    assert isinstance(result, Accepted)
    assert isinstance(result.message, IpcHello)
    assert session.is_handshaked


def test_telemetry_is_refused_before_the_handshake() -> None:
    """Without the sensor declaration the brain cannot tell the UI which nulls are missing sensors
    and which are read failures, so a sample that arrives first has nothing to be interpreted
    against."""
    session, counters = make_session()

    result = session.handle(line(ipc_telemetry_sample()))

    assert isinstance(result, Rejected)
    assert result.code == "handshake_required"
    assert counters.snapshot().handshake_required == 1


def test_telemetry_is_accepted_after_the_handshake() -> None:
    session, counters = make_session()
    session.handle(line(ipc_hello()))

    result = session.handle(line(ipc_telemetry_sample()))

    assert isinstance(result, Accepted)
    assert isinstance(result.message, IpcTelemetrySample)
    assert counters.snapshot().total == 0


def test_a_sidecar_claiming_elevation_is_refused_and_the_connection_dropped() -> None:
    """Local Zero runs every process asInvoker. A sidecar declaring otherwise is either not ours or
    not behaving as described, and the contract makes elevated const false so the brain refuses the
    connection rather than trusting it."""
    session, counters = make_session()
    hello = ipc_hello()
    hello["payload"]["elevated"] = True

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert result.fatal
    assert counters.snapshot().schema_violations == 1
    assert not session.is_handshaked


def test_an_unimplemented_contract_version_is_refused_as_such() -> None:
    session, counters = make_session()
    hello = ipc_hello()
    hello["v"] = 99

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert result.code == "unsupported_version"
    assert counters.snapshot().unsupported_versions == 1


def test_an_unknown_field_is_a_schema_violation() -> None:
    """additionalProperties is false everywhere. That is what stops a field being smuggled past one
    layer in the hope a later one reads it."""
    session, counters = make_session()
    hello = ipc_hello()
    hello["exec"] = "calc.exe"

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"
    assert counters.snapshot().schema_violations == 1


def test_an_unknown_field_nested_in_the_payload_is_a_schema_violation() -> None:
    session, _ = make_session()
    hello = ipc_hello()
    hello["payload"]["exec"] = "calc.exe"

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"


def test_a_sensor_without_a_reason_is_a_schema_violation() -> None:
    """A silent gap is a rejected message: the UI would have nothing to show the user in place of
    the value."""
    session, _ = make_session()
    hello = ipc_hello()
    hello["payload"]["sensors"][3]["unavailable_reason"] = None

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"


def test_a_percentage_outside_the_contract_range_is_a_schema_violation() -> None:
    session, _ = make_session()
    session.handle(line(ipc_hello()))
    sample = ipc_telemetry_sample()
    sample["payload"]["cpu"]["total_percent"] = 140.0

    result = session.handle(line(sample))

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"


def test_malformed_json_is_a_schema_violation() -> None:
    session, counters = make_session()

    result = session.handle("this is not json")

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"
    assert counters.snapshot().schema_violations == 1


def test_a_json_array_is_not_a_message() -> None:
    session, _ = make_session()

    result = session.handle("[1, 2, 3]")

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"


def test_a_second_hello_on_an_established_connection_is_refused() -> None:
    session, _ = make_session()
    session.handle(line(ipc_hello()))

    result = session.handle(line(ipc_hello()))

    assert isinstance(result, Rejected)
    assert result.code == "schema_violation"


def test_one_bad_message_does_not_close_the_connection() -> None:
    session, _ = make_session()

    session.handle("not json")

    assert not session.should_close


def test_repeated_violations_close_the_connection() -> None:
    """One bad message is a peer having a bad moment. Fifty is a peer the brain cannot talk to."""
    session, _ = make_session()

    for _ in range(MAX_VIOLATIONS_PER_CONNECTION):
        session.handle("not json")

    assert session.should_close


def test_a_rejection_names_the_offending_field_without_echoing_its_value() -> None:
    session, _ = make_session()
    hello = ipc_hello()
    hello["payload"]["app_version"] = "not-a-version-and-secret-looking"

    result = session.handle(line(hello))

    assert isinstance(result, Rejected)
    assert "app_version" in result.detail
    assert "secret-looking" not in result.detail


def test_the_accepted_payload_is_the_bytes_that_arrived() -> None:
    """The brain forwards the telemetry payload unchanged, so it keeps the parsed original rather
    than a re-serialization of the model."""
    session, _ = make_session()
    session.handle(line(ipc_hello()))
    sample = ipc_telemetry_sample()

    result = session.handle(line(sample))

    assert isinstance(result, Accepted)
    assert result.raw["payload"] == sample["payload"]


def test_the_handshake_exposes_the_sensor_declaration() -> None:
    """The UI builds its labelled gaps from this, so the brain has to keep it, not just validate
    it."""
    session, _ = make_session()

    session.handle(line(ipc_hello()))

    assert session.hello is not None
    assert any(not sensor.available for sensor in session.hello.payload.sensors)
