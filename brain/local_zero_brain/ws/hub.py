"""Keeps track of connected UI clients and fans frames out to them."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol


class Sender(Protocol):
    """The slice of a WebSocket this module uses.

    Narrow on purpose: the hub is testable with a list-backed fake, and nothing here can reach for
    a part of the framework's socket object that the tests do not also exercise.
    """

    async def send_json(self, data: Any) -> None: ...


class UiHub:
    """The set of UI clients currently entitled to receive telemetry.

    A client is registered only after its hello has been accepted, which is how "the brain streams
    nothing before client.hello" is enforced: an unregistered socket is simply not in the fan-out.
    """

    def __init__(self, log: Callable[[str], None] = lambda _: None) -> None:
        self._clients: set[Sender] = set()
        self._lock = asyncio.Lock()
        self._log = log

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def register(self, client: Sender) -> None:
        async with self._lock:
            self._clients.add(client)

    async def unregister(self, client: Sender) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Sends to every registered client.

        A client that fails is dropped rather than allowed to break the fan-out. One browser tab
        closing mid-send must not stop telemetry reaching the others, and it must not take down the
        link that produced the frame.
        """
        async with self._lock:
            recipients = list(self._clients)

        failed: list[Sender] = []
        for client in recipients:
            try:
                await client.send_json(message)
            except Exception as error:  # noqa: BLE001 - any send failure means the client is gone
                self._log(f"dropping a UI client after a failed send: {error!r}")
                failed.append(client)

        if failed:
            async with self._lock:
                self._clients.difference_update(failed)
