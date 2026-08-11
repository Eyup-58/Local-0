"""The system link: turns pipe events into the frames the UI sees.

This is where the brain's honesty about connectivity lives. A dead sidecar and an idle machine look
identical if you only watch the numbers - both stop changing. The link makes the difference
explicit by emitting system.status on every transition, so the UI can mark data stale instead of
presenting the last sample as live.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from local_zero_brain.contracts.common import CONTRACT_VERSION, MAX_ERROR_MESSAGE_LENGTH
from local_zero_brain.contracts.ipc import IpcError, IpcHello, IpcTelemetrySample
from local_zero_brain.ipc.pipe_client import (
    PipeConnected,
    PipeDisconnected,
    PipeEvent,
    PipeLine,
    SystemPipeClient,
)
from local_zero_brain.ipc.session import Accepted, Rejected, SystemSession
from local_zero_brain.metrics import DropCounters
from local_zero_brain.ws.messages import DEFAULT_POLL_INTERVAL_MS, WsMessageFactory, utc_now

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]

_STARTUP_REASON = "The system layer has not connected yet. No telemetry is available."


@dataclass(frozen=True, slots=True)
class SystemLinkState:
    """What the brain currently knows about the system layer.

    Frozen and replaced wholesale rather than mutated, so a reader that has already taken a copy
    keeps a coherent picture instead of watching fields change out from under it.
    """

    #: True only once the pipe is open **and** a valid hello has arrived. A pipe with no handshake
    #: is not a system layer the brain can interpret.
    connected: bool = False
    since: str = field(default_factory=utc_now)
    reason: str | None = _STARTUP_REASON
    #: Forwarded verbatim from the sidecar's hello, so the UI's labelled gaps say exactly what the
    #: sidecar said.
    sensors: list[dict[str, Any]] = field(default_factory=list)
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS


class SystemLink:
    """Consumes pipe events, validates them, and broadcasts the results to the UI."""

    def __init__(
        self,
        *,
        queue: asyncio.Queue[PipeEvent],
        counters: DropCounters,
        broadcast: Broadcast,
        client: SystemPipeClient | None = None,
        messages: WsMessageFactory | None = None,
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._queue = queue
        self._counters = counters
        self._broadcast = broadcast
        self._client = client
        self._messages = messages or WsMessageFactory()
        self._log = log

        self._state = SystemLinkState()
        self._session: SystemSession | None = None

    @property
    def state(self) -> SystemLinkState:
        return self._state

    async def run(self) -> None:
        """Drains the event queue until cancelled."""
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            except Exception as error:  # noqa: BLE001 - one bad event must not end the loop
                self._log(f"failed to handle a pipe event: {error!r}")

    async def _handle(self, event: PipeEvent) -> None:
        if isinstance(event, PipeConnected):
            # A fresh session per connection, so the handshake gate and seq numbering both reset
            # exactly when the contract says they do.
            self._session = SystemSession(self._counters)
            return

        if isinstance(event, PipeDisconnected):
            await self._on_disconnected(event.reason)
            return

        await self._on_line(event)

    async def _on_line(self, event: PipeLine) -> None:
        if self._session is None:
            # A line before the connection event. Nothing sensible to validate it against.
            self._counters.record_schema_violation()
            return

        result = self._session.handle(event.text)

        if isinstance(result, Rejected):
            await self._on_rejected(result)
            return

        await self._on_accepted(result)

    async def _on_rejected(self, rejected: Rejected) -> None:
        self._log(f"dropped a message from the system layer: {rejected.detail}")

        if self._client is not None and rejected.code in ("handshake_required", "unsupported_version"):
            # The contract defines these replies. They are best effort: if the pipe has already
            # gone, the drop is still recorded and the connection is closing anyway.
            self._client.send(_ipc_error_line(rejected.code, rejected.detail))

        if rejected.fatal or (self._session is not None and self._session.should_close):
            self._log("closing the connection to the system layer")
            await self._on_disconnected(
                "The system layer sent messages the contract does not allow. Telemetry is paused."
            )

    async def _on_accepted(self, accepted: Accepted) -> None:
        message = accepted.message

        if isinstance(message, IpcHello):
            await self._on_hello(accepted.raw)
            return

        if isinstance(message, IpcTelemetrySample):
            await self._broadcast(self._messages.telemetry_sample(accepted.raw["payload"]))
            return

        if isinstance(message, IpcError):
            # The sidecar reporting a sensor fault. The UI's vocabulary for "the system layer
            # cannot tell you right now" is system_unavailable.
            await self._broadcast(
                self._messages.error(code="system_unavailable", message=message.payload.message)
            )

    async def _on_hello(self, raw: dict[str, Any]) -> None:
        payload = raw["payload"]
        self._state = replace(
            self._state,
            connected=True,
            since=utc_now(),
            reason=None,
            sensors=payload["sensors"],
            poll_interval_ms=payload["poll_interval_ms"],
        )
        self._log("the system layer handshaked")
        await self._broadcast_status()

    async def _on_disconnected(self, reason: str) -> None:
        if not self._state.connected and self._state.reason == reason:
            # Already disconnected for this reason. Repeating it on every failed reconnect would
            # bury the transition that mattered.
            return

        self._session = None
        self._state = replace(
            self._state,
            connected=False,
            since=utc_now(),
            reason=reason,
            # The declaration is dropped with the connection: it described a sidecar that is gone,
            # and the next one may declare something different.
            sensors=[],
        )
        await self._broadcast_status()

    async def _broadcast_status(self) -> None:
        await self._broadcast(
            self._messages.system_status(
                connected=self._state.connected,
                since=self._state.since,
                reason=self._state.reason,
                sensors=self._state.sensors,
            )
        )


def _ipc_error_line(code: str, detail: str) -> str:
    """An IPC-shaped error for the sidecar, serialized as one NDJSON line.

    Built here rather than through the WebSocket factory because it travels the other boundary,
    where the error code enum is a different set: the IPC contract has sensor_read_failed where the
    WebSocket contract has system_unavailable.
    """
    message = detail[:MAX_ERROR_MESSAGE_LENGTH] or "The message was refused."

    return json.dumps(
        {
            "v": CONTRACT_VERSION,
            "id": str(uuid4()),
            "ts": utc_now(),
            "type": "error",
            "payload": {"code": code, "message": message, "in_reply_to": None},
        }
    )
