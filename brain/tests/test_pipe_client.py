"""The pywin32 pipe client, against a real Windows named pipe.

CONTRACTS.md section 8 left the transport open, to be "decided in M1 against working code, not
guessed at here". This file is that working code's evidence: a real named pipe, a real reader
thread, and events arriving on the event loop through call_soon_threadsafe.

A mocked win32file would prove only that the code calls the functions it calls.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from uuid import uuid4

import pytest
import win32file
import win32pipe

from local_zero_brain.ipc.pipe_client import (
    PipeConnected,
    PipeDisconnected,
    PipeEvent,
    PipeLine,
    SystemPipeClient,
)
from local_zero_brain.metrics import DropCounters

_EVENT_TIMEOUT_SECONDS = 10.0
_PIPE_BUFFER_BYTES = 64 * 1024


class FakeSidecar:
    """A minimal stand-in for the C# sidecar: one pipe, one connection, scripted writes."""

    def __init__(self, on_connected: Callable[[int], None]) -> None:
        self.name = f"LocalZero.Test.{uuid4().hex}"
        self.received: list[bytes] = []
        self._path = rf"\\.\pipe\{self.name}"
        self._on_connected = on_connected
        self._listening = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeSidecar:
        self._thread.start()
        assert self._listening.wait(_EVENT_TIMEOUT_SECONDS), "the fake sidecar never began listening"
        return self

    def __exit__(self, *_: object) -> None:
        self._thread.join(_EVENT_TIMEOUT_SECONDS)

    def _serve(self) -> None:
        handle = win32pipe.CreateNamedPipe(
            self._path,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            _PIPE_BUFFER_BYTES,
            _PIPE_BUFFER_BYTES,
            0,
            None,
        )
        self._listening.set()

        try:
            win32pipe.ConnectNamedPipe(handle, None)
            self._on_connected(handle)
        finally:
            win32file.CloseHandle(handle)


async def drain(queue: asyncio.Queue[PipeEvent], count: int) -> list[PipeEvent]:
    return [await asyncio.wait_for(queue.get(), _EVENT_TIMEOUT_SECONDS) for _ in range(count)]


def start_client(sidecar: FakeSidecar) -> tuple[SystemPipeClient, asyncio.Queue[PipeEvent], DropCounters]:
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    counters = DropCounters()
    client = SystemPipeClient(
        queue=queue,
        counters=counters,
        loop=asyncio.get_running_loop(),
        pipe_name=sidecar.name,
        reconnect_seconds=0.05,
    )
    client.start()
    return client, queue, counters


@pytest.mark.asyncio
async def test_lines_written_to_a_real_pipe_arrive_as_events() -> None:
    def write_two_lines(handle: int) -> None:
        win32file.WriteFile(handle, b'{"a":1}\n{"b":2}\n')

    with FakeSidecar(write_two_lines) as sidecar:
        client, queue, _ = start_client(sidecar)
        try:
            events = await drain(queue, 3)
        finally:
            client.stop()

    assert isinstance(events[0], PipeConnected)
    assert [event.text for event in events[1:] if isinstance(event, PipeLine)] == ['{"a":1}', '{"b":2}']


@pytest.mark.asyncio
async def test_a_message_split_across_writes_is_reassembled() -> None:
    """The pipe is a byte stream. Nothing guarantees a write boundary is a message boundary, and
    assuming otherwise produces a parser that works until the machine is busy."""

    def write_in_pieces(handle: int) -> None:
        win32file.WriteFile(handle, b'{"a"')
        win32file.WriteFile(handle, b":1}\n")

    with FakeSidecar(write_in_pieces) as sidecar:
        client, queue, _ = start_client(sidecar)
        try:
            events = await drain(queue, 2)
        finally:
            client.stop()

    assert isinstance(events[1], PipeLine)
    assert events[1].text == '{"a":1}'


@pytest.mark.asyncio
async def test_the_sidecar_going_away_produces_a_disconnect_with_a_user_safe_reason() -> None:
    """This is what lets the UI degrade honestly rather than freezing on the last sample."""

    def write_once_then_close(handle: int) -> None:
        win32file.WriteFile(handle, b'{"a":1}\n')

    with FakeSidecar(write_once_then_close) as sidecar:
        client, queue, _ = start_client(sidecar)
        try:
            events = await drain(queue, 3)
        finally:
            client.stop()

    disconnected = events[2]
    assert isinstance(disconnected, PipeDisconnected)
    assert disconnected.reason
    # User-safe prose: no pipe path, no Win32 error number, no exception text.
    assert "\\\\.\\pipe" not in disconnected.reason
    assert "winerror" not in disconnected.reason.lower()


@pytest.mark.asyncio
async def test_the_client_can_write_back_to_the_sidecar() -> None:
    """The contract's error replies - handshake_required, unsupported_version - travel this way."""
    received: list[bytes] = []
    connected = threading.Event()

    def read_one_line(handle: int) -> None:
        connected.set()
        _, data = win32file.ReadFile(handle, _PIPE_BUFFER_BYTES)
        received.append(data)

    with FakeSidecar(read_one_line) as sidecar:
        client, queue, _ = start_client(sidecar)
        try:
            await drain(queue, 1)
            assert connected.wait(_EVENT_TIMEOUT_SECONDS)
            assert client.send('{"type":"error"}')
            await asyncio.sleep(0.2)
        finally:
            client.stop()

    assert received == [b'{"type":"error"}\n']


@pytest.mark.asyncio
async def test_a_client_with_nothing_to_read_still_stops_promptly() -> None:
    """Regression.

    The first version of the client used a synchronous ReadFile. A synchronous read on a pipe with
    no data blocks until data arrives, and closing the handle from another thread does not reliably
    unblock it on Windows - so the reader thread could not be stopped and the process would not shut
    down. The pipe is opened overlapped precisely so a stop request can interrupt a pending read.
    """

    def stay_connected_and_silent(handle: int) -> None:
        # Holds the connection open, sending nothing. This is the state a healthy sidecar is in
        # between ticks, so it is also the state the brain is usually shut down from.
        time.sleep(3.0)

    with FakeSidecar(stay_connected_and_silent) as sidecar:
        client, queue, _ = start_client(sidecar)
        await drain(queue, 1)

        started = time.monotonic()
        await asyncio.to_thread(client.stop, 5.0)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"stop() took {elapsed:.2f}s; the reader thread was not interruptible"


@pytest.mark.asyncio
async def test_sending_without_a_live_pipe_reports_failure_rather_than_raising() -> None:
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    client = SystemPipeClient(
        queue=queue,
        counters=DropCounters(),
        loop=asyncio.get_running_loop(),
        pipe_name=f"LocalZero.Absent.{uuid4().hex}",
    )

    assert client.send('{"type":"error"}') is False


@pytest.mark.asyncio
async def test_telemetry_is_discarded_when_the_consumer_stalls() -> None:
    """Bounded growth. A stalled UI must not turn into an ever-growing backlog in the brain - the
    consumer sees the loss as a gap in seq, which is what the contract says a gap means."""
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    counters = DropCounters()
    client = SystemPipeClient(
        queue=queue,
        counters=counters,
        loop=asyncio.get_running_loop(),
        pipe_name="unused",
        max_queued_lines=2,
    )

    client._offer(PipeLine("first"))
    client._offer(PipeLine("second"))
    client._offer(PipeLine("third"))

    assert queue.qsize() == 2
    assert counters.snapshot().backpressure_drops == 1


@pytest.mark.asyncio
async def test_status_events_are_never_discarded_under_backpressure() -> None:
    """Losing a status transition would leave the UI's idea of connectivity permanently wrong,
    which is the exact failure system.status exists to prevent."""
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    counters = DropCounters()
    client = SystemPipeClient(
        queue=queue,
        counters=counters,
        loop=asyncio.get_running_loop(),
        pipe_name="unused",
        max_queued_lines=1,
    )

    client._offer(PipeLine("first"))
    client._offer(PipeDisconnected("gone"))

    assert queue.qsize() == 2
    assert counters.snapshot().backpressure_drops == 0
