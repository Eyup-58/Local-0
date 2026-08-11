"""The system link: pipe events in, UI frames out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from conftest import ipc_hello, ipc_telemetry_sample
from local_zero_brain.ipc.pipe_client import PipeConnected, PipeDisconnected, PipeEvent, PipeLine
from local_zero_brain.link import SystemLink
from local_zero_brain.metrics import DropCounters


class Recorder:
    """Collects broadcast frames, standing in for the hub."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.frames.append(message)

    def of_type(self, message_type: str) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame["type"] == message_type]


class SendRecorder:
    """Stands in for the pipe client, capturing what the brain writes back to the sidecar."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, message: str) -> bool:
        self.sent.append(json.loads(message))
        return True


async def feed(link: SystemLink, queue: asyncio.Queue[PipeEvent], events: list[PipeEvent]) -> None:
    """Runs the link just long enough to drain the events handed to it."""
    for event in events:
        queue.put_nowait(event)

    task = asyncio.create_task(link.run())
    while not queue.empty():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def build() -> tuple[SystemLink, asyncio.Queue[PipeEvent], Recorder, SendRecorder, DropCounters]:
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    recorder = Recorder()
    client = SendRecorder()
    counters = DropCounters()
    link = SystemLink(
        queue=queue,
        counters=counters,
        broadcast=recorder,
        client=client,  # type: ignore[arg-type]
    )
    return link, queue, recorder, client, counters


@pytest.mark.asyncio
async def test_a_handshake_marks_the_system_connected() -> None:
    link, queue, recorder, _, _ = build()

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(ipc_hello()))])

    status = recorder.of_type("system.status")
    assert len(status) == 1
    assert status[0]["payload"]["connected"] is True
    assert status[0]["payload"]["reason"] is None


@pytest.mark.asyncio
async def test_the_sensor_declaration_is_forwarded_verbatim() -> None:
    """The UI's labelled gaps are built from this, so it must say exactly what the sidecar said."""
    link, queue, recorder, _, _ = build()
    hello = ipc_hello()

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(hello))])

    status = recorder.of_type("system.status")[0]
    assert status["payload"]["sensors"] == hello["payload"]["sensors"]


@pytest.mark.asyncio
async def test_a_telemetry_payload_is_forwarded_unchanged() -> None:
    """M1 forwards the sample unchanged and re-stamps only the envelope. Any divergence has to be
    written into docs/CONTRACTS.md rather than appearing quietly here."""
    link, queue, recorder, _, _ = build()
    sample = ipc_telemetry_sample()

    await feed(
        link,
        queue,
        [PipeConnected(), PipeLine(json.dumps(ipc_hello())), PipeLine(json.dumps(sample))],
    )

    forwarded = recorder.of_type("telemetry.sample")
    assert len(forwarded) == 1
    assert forwarded[0]["payload"] == sample["payload"]


@pytest.mark.asyncio
async def test_the_forwarded_envelope_is_restamped() -> None:
    link, queue, recorder, _, _ = build()
    sample = ipc_telemetry_sample()

    await feed(
        link,
        queue,
        [PipeConnected(), PipeLine(json.dumps(ipc_hello())), PipeLine(json.dumps(sample))],
    )

    forwarded = recorder.of_type("telemetry.sample")[0]
    assert forwarded["id"] != sample["id"]
    assert forwarded["v"] == sample["v"]


@pytest.mark.asyncio
async def test_telemetry_before_a_handshake_is_dropped_and_answered() -> None:
    link, queue, recorder, client, counters = build()

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(ipc_telemetry_sample()))])

    assert recorder.of_type("telemetry.sample") == []
    assert counters.snapshot().handshake_required == 1
    assert client.sent[0]["payload"]["code"] == "handshake_required"


@pytest.mark.asyncio
async def test_an_unsupported_version_is_answered_on_the_pipe() -> None:
    link, queue, _, client, counters = build()
    hello = ipc_hello()
    hello["v"] = 99

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(hello))])

    assert counters.snapshot().unsupported_versions == 1
    assert client.sent[0]["payload"]["code"] == "unsupported_version"


@pytest.mark.asyncio
async def test_the_sidecar_dying_marks_the_data_stale() -> None:
    """Without this the UI cannot tell a dead sidecar from an idle machine - both simply stop
    changing."""
    link, queue, recorder, _, _ = build()

    await feed(
        link,
        queue,
        [
            PipeConnected(),
            PipeLine(json.dumps(ipc_hello())),
            PipeDisconnected("The system layer closed the connection."),
        ],
    )

    status = recorder.of_type("system.status")
    assert status[-1]["payload"]["connected"] is False
    assert status[-1]["payload"]["reason"] == "The system layer closed the connection."


@pytest.mark.asyncio
async def test_the_declaration_is_dropped_with_the_connection() -> None:
    """It described a sidecar that is gone, and the next one may declare something different."""
    link, queue, recorder, _, _ = build()

    await feed(
        link,
        queue,
        [PipeConnected(), PipeLine(json.dumps(ipc_hello())), PipeDisconnected("gone")],
    )

    assert recorder.of_type("system.status")[-1]["payload"]["sensors"] == []
    assert link.state.connected is False


@pytest.mark.asyncio
async def test_repeated_disconnect_reasons_are_not_rebroadcast() -> None:
    """Reconnect attempts fail once a second while the sidecar is down. Announcing each one would
    bury the transition that mattered."""
    link, queue, recorder, _, _ = build()

    await feed(
        link,
        queue,
        [PipeDisconnected("not running"), PipeDisconnected("not running"), PipeDisconnected("not running")],
    )

    assert len(recorder.of_type("system.status")) <= 1


@pytest.mark.asyncio
async def test_a_sidecar_claiming_elevation_ends_the_connection() -> None:
    link, queue, recorder, _, _ = build()
    hello = ipc_hello()
    hello["payload"]["elevated"] = True

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(hello))])

    assert link.state.connected is False
    assert recorder.of_type("system.status")[-1]["payload"]["connected"] is False


@pytest.mark.asyncio
async def test_a_sensor_fault_reaches_the_ui_as_system_unavailable() -> None:
    link, queue, recorder, _, _ = build()
    fault = {
        "v": 1,
        "id": "8d1c4b02-6e39-4f7a-b2c5-91af3d0e7c48",
        "ts": "2026-08-11T09:14:03.121Z",
        "type": "error",
        "payload": {
            "code": "sensor_read_failed",
            "message": "The gpu sensors could not be read.",
            "in_reply_to": None,
        },
    }

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(ipc_hello())), PipeLine(json.dumps(fault))])

    errors = recorder.of_type("error")
    assert errors[0]["payload"]["code"] == "system_unavailable"


@pytest.mark.asyncio
async def test_a_malformed_line_does_not_stop_the_link() -> None:
    link, queue, recorder, _, counters = build()

    await feed(
        link,
        queue,
        [
            PipeConnected(),
            PipeLine(json.dumps(ipc_hello())),
            PipeLine("not json"),
            PipeLine(json.dumps(ipc_telemetry_sample())),
        ],
    )

    assert counters.snapshot().schema_violations == 1
    assert len(recorder.of_type("telemetry.sample")) == 1


@pytest.mark.asyncio
async def test_the_poll_interval_is_taken_from_the_sidecar() -> None:
    link, queue, _, _, _ = build()
    hello = ipc_hello()
    hello["payload"]["poll_interval_ms"] = 500

    await feed(link, queue, [PipeConnected(), PipeLine(json.dumps(hello))])

    assert link.state.poll_interval_ms == 500
