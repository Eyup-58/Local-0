"""Windows named pipe client, on a reader thread.

This is the resolution of the open question CONTRACTS.md section 8 left for M1.

Python's standard library has no Windows named pipe support, so the choice was between ``pywin32``
and abandoning the pipe for a ``127.0.0.1`` socket. The socket is simpler code and natively
asyncio-aware, and it forfeits the only reason the transport was chosen: a pipe carries an
OS-enforced ACL, while a loopback listener is reachable by every process running as any user on the
machine and can only be defended with an application-level secret that has to be stored somewhere.
That somewhere becomes the new weakest link, on a channel whose messages will eventually authorize
OS actions.

So: ``pywin32``. Its handles are not asyncio-aware, which costs one dedicated reader thread feeding
an ``asyncio.Queue`` through ``loop.call_soon_threadsafe``. One extra moving part, a well
understood shape, and the security property intact.

**The pipe is opened for overlapped I/O**, and that is not an optimisation. A synchronous
``ReadFile`` on a pipe with no data blocks until data arrives, and on Windows closing the handle
from another thread does not reliably unblock it - which means a reader thread that cannot be
stopped, and a process that will not shut down cleanly. Measured here: the first version of this
file used a blocking read and the test suite hung on shutdown.

Polling with ``PeekNamedPipe`` would also fix it, at the price of a syscall every few milliseconds
forever. This layer reports on machine resource use and has no business burning idle CPU to do it
(docs/PERFORMANCE.md budget P5), so the read waits on an event instead and costs nothing while
idle.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass

import pywintypes
import win32event
import win32file
import win32pipe
import winerror

from local_zero_brain.ipc.framing import LineAssembler, encode_line
from local_zero_brain.metrics import DropCounters

DEFAULT_PIPE_NAME = "LocalZero.System.v1"

_READ_CHUNK_BYTES = 64 * 1024
_DEFAULT_RECONNECT_SECONDS = 1.0
_PIPE_BUSY_WAIT_MS = 2000
_SHUTTING_DOWN = "The brain is shutting down."

#: How many unconsumed events may queue up before telemetry lines start being discarded.
#:
#: At 1 Hz this is only reached when the consumer has genuinely stalled. Status events are never
#: discarded regardless of depth: losing one would leave the UI's idea of connectivity permanently
#: wrong, which is precisely the failure system.status exists to prevent.
DEFAULT_MAX_QUEUED_LINES = 256


@dataclass(frozen=True, slots=True)
class PipeConnected:
    """The pipe opened. Says nothing about the handshake, which is the session's business."""


@dataclass(frozen=True, slots=True)
class PipeLine:
    text: str


@dataclass(frozen=True, slots=True)
class PipeDisconnected:
    """The pipe closed, with user-safe prose explaining why."""

    reason: str


PipeEvent = PipeConnected | PipeLine | PipeDisconnected


class SystemPipeClient:
    """Connects to the sidecar's pipe, reads lines, and reconnects when it goes away.

    The sidecar owns the pipe and the brain is the client, which is the right way round: the
    sidecar's ACL decides who may connect, and the brain restarting does not require the sidecar to
    do anything.
    """

    def __init__(
        self,
        *,
        queue: asyncio.Queue[PipeEvent],
        counters: DropCounters,
        loop: asyncio.AbstractEventLoop | None = None,
        pipe_name: str = DEFAULT_PIPE_NAME,
        reconnect_seconds: float = _DEFAULT_RECONNECT_SECONDS,
        max_queued_lines: int = DEFAULT_MAX_QUEUED_LINES,
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._queue = queue
        self._counters = counters
        self._loop = loop or asyncio.get_event_loop()
        self._path = rf"\\.\pipe\{pipe_name}"
        self._reconnect_seconds = reconnect_seconds
        self._max_queued_lines = max_queued_lines
        self._log = log

        self._stopping = threading.Event()
        #: The Win32 half of the stop signal. A pending overlapped read waits on this alongside its
        #: own completion event, so a stop request interrupts a read that has no data coming.
        self._stop_handle = win32event.CreateEvent(None, True, False, None)
        self._thread: threading.Thread | None = None
        self._handle = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the pipe client is already running")

        self._thread = threading.Thread(target=self._run, name="local-zero-pipe-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signals the reader thread, waits for it, and only then closes the handle.

        The order matters. Closing first would leave the reader holding a handle that has already
        gone, and would not have woken it up anyway.
        """
        self._stopping.set()
        win32event.SetEvent(self._stop_handle)

        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

        self._close_handle()

    def send(self, message: str) -> bool:
        """Writes one message back to the sidecar. Returns False when there is no live pipe.

        Used for the error replies the contract defines - ``handshake_required`` and
        ``unsupported_version``. Writes are small and infrequent, so they go out under a lock from
        whatever thread is calling rather than through a second queue.
        """
        with self._write_lock:
            handle = self._handle
            if handle is None:
                return False

            overlapped = _new_overlapped()
            try:
                win32file.WriteFile(handle, encode_line(message), overlapped)
                # The handle is overlapped, so the write may complete asynchronously. Waiting here
                # keeps send() a simple synchronous call for its callers.
                win32file.GetOverlappedResult(handle, overlapped, True)
            except pywintypes.error as error:
                self._log(f"pipe write failed: {_describe(error)}")
                return False
            finally:
                win32file.CloseHandle(overlapped.hEvent)

        return True

    def _run(self) -> None:
        while not self._stopping.is_set():
            if not self._connect():
                # wait() rather than sleep(): a stop request interrupts it immediately.
                self._stopping.wait(self._reconnect_seconds)
                continue

            self._emit(PipeConnected())
            reason = self._read_until_disconnect()
            self._close_handle()
            self._emit(PipeDisconnected(reason))

            self._stopping.wait(self._reconnect_seconds)

    def _connect(self) -> bool:
        try:
            handle = win32file.CreateFile(
                self._path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                # Overlapped, so a read with nothing to read can still be interrupted. See the
                # module docstring - this is what makes shutdown work.
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as error:
            if error.winerror == winerror.ERROR_PIPE_BUSY:
                # Every instance is taken. Wait for one rather than spinning on CreateFile.
                try:
                    win32pipe.WaitNamedPipe(self._path, _PIPE_BUSY_WAIT_MS)
                except pywintypes.error:
                    pass
            return False

        with self._write_lock:
            self._handle = handle

        return True

    def _read_until_disconnect(self) -> str:
        assembler = LineAssembler(
            on_oversized=self._counters.record_oversized_line,
            on_undecodable=self._counters.record_undecodable_line,
        )
        overlapped = _new_overlapped()
        buffer = win32file.AllocateReadBuffer(_READ_CHUNK_BYTES)

        try:
            while not self._stopping.is_set():
                chunk = self._read_once(overlapped, buffer)
                if isinstance(chunk, str):
                    return chunk

                for line in assembler.feed(chunk):
                    self._emit(PipeLine(line))

            return _SHUTTING_DOWN
        finally:
            win32file.CloseHandle(overlapped.hEvent)

    def _read_once(self, overlapped: object, buffer: object) -> bytes | str:
        """One overlapped read. Returns the bytes read, or prose explaining why reading stopped."""
        win32event.ResetEvent(overlapped.hEvent)

        try:
            win32file.ReadFile(self._handle, buffer, overlapped)
        except pywintypes.error as error:
            return _describe(error)

        signalled = win32event.WaitForMultipleObjects(
            [overlapped.hEvent, self._stop_handle], False, win32event.INFINITE
        )

        if signalled != win32event.WAIT_OBJECT_0:
            # Stop was requested while the read was pending. Cancel it so the buffer is not written
            # to after this frame goes away.
            win32file.CancelIo(self._handle)
            return _SHUTTING_DOWN

        try:
            transferred = win32file.GetOverlappedResult(self._handle, overlapped, True)
        except pywintypes.error as error:
            return _describe(error)

        if transferred == 0:
            return "The system layer closed the connection. Telemetry is paused."

        return bytes(buffer[:transferred])

    def _close_handle(self) -> None:
        with self._write_lock:
            handle, self._handle = self._handle, None

        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except pywintypes.error:
                # Already gone. Nothing to recover and nothing worth reporting.
                pass

    def _emit(self, event: PipeEvent) -> None:
        try:
            self._loop.call_soon_threadsafe(self._offer, event)
        except RuntimeError:
            # The loop closed while this thread was still reading. Shutting down.
            pass

    def _offer(self, event: PipeEvent) -> None:
        """Runs on the event loop thread, which is what makes the depth check safe to act on."""
        if isinstance(event, PipeLine) and self._queue.qsize() >= self._max_queued_lines:
            self._counters.record_backpressure_drop()
            return

        self._queue.put_nowait(event)


def _new_overlapped() -> pywintypes.OVERLAPPED:
    """An OVERLAPPED with a manual-reset completion event.

    Manual reset because the event is waited on together with the stop handle, and an auto-reset
    event consumed by a wait that lost the race would be lost entirely.
    """
    overlapped = pywintypes.OVERLAPPED()
    overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
    return overlapped


def _describe(error: pywintypes.error) -> str:
    """User-safe prose for a Win32 failure.

    The contract forbids stack traces and paths in the ``reason`` field that carries this to the
    UI, so the pipe path and the raw exception stay out of it.
    """
    if error.winerror in (winerror.ERROR_BROKEN_PIPE, winerror.ERROR_PIPE_NOT_CONNECTED):
        return "The system layer closed the connection. Telemetry is paused; displayed values are no longer live."

    if error.winerror == winerror.ERROR_FILE_NOT_FOUND:
        return "The system layer is not running. Telemetry is unavailable until it starts."

    if error.winerror == winerror.ERROR_ACCESS_DENIED:
        return "Access to the system layer was denied. Both processes must run as the same user."

    return "The connection to the system layer failed. Telemetry is paused."
