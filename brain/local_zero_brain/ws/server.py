"""The FastAPI application: a WebSocket for the UI, fed by the system link.

Bound to 127.0.0.1 only. The brain accepts no connection from off the machine, and there is no
configuration switch to change that in M1 - a local assistant that starts listening on a LAN
interface because a flag was set wrong is a different product with a different threat model.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from local_zero_brain.contracts.common import CONTRACT_VERSION
from local_zero_brain.contracts.ws import CLIENT_MESSAGE_ADAPTER
from local_zero_brain.ipc.pipe_client import DEFAULT_PIPE_NAME, PipeEvent, SystemPipeClient
from local_zero_brain.link import SystemLink
from local_zero_brain.metrics import DropCounters
from local_zero_brain.ws.hub import UiHub
from local_zero_brain.ws.messages import WsMessageFactory

#: Loopback only. See the module docstring.
BIND_HOST = "127.0.0.1"
BIND_PORT = 8765

#: WebSocket close codes. 1002 is "protocol error", which is what a client that will not handshake
#: correctly has committed.
_CLOSE_PROTOCOL_ERROR = 1002


@dataclass(slots=True)
class BrainServices:
    """Everything the app wires together, kept addressable so tests can reach in."""

    counters: DropCounters
    hub: UiHub
    link: SystemLink
    messages: WsMessageFactory
    client: SystemPipeClient | None = None
    reader: asyncio.Task[None] | None = None


def create_app(
    *,
    pipe_name: str = DEFAULT_PIPE_NAME,
    start_pipe_client: bool = True,
    log: Any = print,
) -> FastAPI:
    """Builds the application.

    ``start_pipe_client`` exists so tests can drive the link directly instead of standing up a
    Windows pipe for every WebSocket case.
    """
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    counters = DropCounters()
    hub = UiHub(log=log)
    messages = WsMessageFactory()

    services = BrainServices(counters=counters, hub=hub, messages=messages, link=None)  # type: ignore[arg-type]

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client: SystemPipeClient | None = None
        if start_pipe_client:
            client = SystemPipeClient(
                queue=queue,
                counters=counters,
                loop=asyncio.get_running_loop(),
                pipe_name=pipe_name,
                log=log,
            )

        services.client = client
        services.link = SystemLink(
            queue=queue,
            counters=counters,
            broadcast=hub.broadcast,
            client=client,
            messages=messages,
            log=log,
        )

        if client is not None:
            client.start()

        services.reader = asyncio.create_task(services.link.run(), name="system-link")
        try:
            yield
        finally:
            services.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await services.reader
            if client is not None:
                client.stop()

            snapshot = counters.snapshot()
            log(f"stopped. dropped messages: {snapshot}")

    app = FastAPI(title="Local Zero brain", version="0.1.0", lifespan=lifespan)
    app.state.services = services

    @app.websocket("/ws")
    async def telemetry_socket(websocket: WebSocket) -> None:
        await _serve_ui(websocket, services)

    return app


async def _serve_ui(websocket: WebSocket, services: BrainServices) -> None:
    await websocket.accept()

    if not await _complete_handshake(websocket, services):
        return

    await services.hub.register(websocket)
    try:
        # The UI sends nothing else in M1. Reading anyway is what detects the socket closing, and
        # it is where a frame the UI is not allowed to send gets refused rather than ignored.
        while True:
            frame = await websocket.receive_text()
            await _refuse_unexpected_frame(websocket, services, frame)
    except WebSocketDisconnect:
        pass
    finally:
        await services.hub.unregister(websocket)


async def _complete_handshake(websocket: WebSocket, services: BrainServices) -> bool:
    """Requires a valid client.hello before anything is streamed.

    Returns False when the socket has been closed and the caller should stop.
    """
    try:
        frame = await websocket.receive_text()
    except WebSocketDisconnect:
        return False

    try:
        CLIENT_MESSAGE_ADAPTER.validate_json(frame)
    except ValidationError as error:
        code = "unsupported_version" if _is_version_mismatch(error) else "schema_violation"
        await _close_with_error(
            websocket,
            services,
            code=code,
            message="The first frame must be a valid client.hello for contract version "
            f"{CONTRACT_VERSION}.",
        )
        return False

    state = services.link.state
    await websocket.send_json(
        services.messages.server_hello(
            poll_interval_ms=state.poll_interval_ms,
            system_connected=state.connected,
            sensors=state.sensors,
        )
    )
    return True


async def _refuse_unexpected_frame(websocket: WebSocket, services: BrainServices, frame: str) -> None:
    """The UI holds no authority.

    It can approve or reject what the brain has already resolved; it cannot construct anything. In
    M1 it has nothing at all to send after its hello, so anything that arrives is refused and
    counted rather than parsed for meaning.
    """
    services.counters.record_schema_violation()
    await websocket.send_json(
        services.messages.error(
            code="schema_violation",
            message="The UI may not send this message. Only client.hello is accepted on this socket.",
        )
    )


async def _close_with_error(websocket: WebSocket, services: BrainServices, *, code: Any, message: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json(services.messages.error(code=code, message=message))
        await websocket.close(code=_CLOSE_PROTOCOL_ERROR)


def _is_version_mismatch(error: ValidationError) -> bool:
    return any(item["loc"][-1:] == ("v",) for item in error.errors())


#: The importable application for `uvicorn local_zero_brain.ws.server:app`.
app = create_app()
