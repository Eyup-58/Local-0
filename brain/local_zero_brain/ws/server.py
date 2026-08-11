"""The FastAPI application: a WebSocket for the UI, fed by the system link.

Bound to 127.0.0.1 only. The brain accepts no connection from off the machine, and there is no
configuration switch to change that in M1 - a local assistant that starts listening on a LAN
interface because a flag was set wrong is a different product with a different threat model.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from local_zero_brain.audit import AuditLog
from local_zero_brain.capabilities.guard import Guard, Invocation, Pending, Verdict
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.capabilities.paths import workspace_root
from local_zero_brain.contracts.common import CONTRACT_VERSION
from local_zero_brain.contracts.ws import CLIENT_HELLO_ADAPTER, CLIENT_MESSAGE_ADAPTER, ApprovalDecision, TrustSet
from local_zero_brain.ipc.pipe_client import DEFAULT_PIPE_NAME, PipeEvent, SystemPipeClient
from local_zero_brain.link import SystemLink
from local_zero_brain.metrics import DropCounters
from local_zero_brain.trust import TrustStore
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
    guard: Guard
    trust: TrustStore
    #: Guard verdicts waiting on a human, by request_id.
    #:
    #: Held here rather than in the queue because executing an approved invocation needs the
    #: Capability object and its resolved arguments, and the queue deliberately stores only what the
    #: user was shown. The queue remains the thing that enforces one-answer-per-request.
    awaiting: dict[str, Pending] = field(default_factory=dict)
    client: SystemPipeClient | None = None
    reader: asyncio.Task[None] | None = None

    async def invoke(self, invocation: Invocation) -> Verdict:
        """The single entry point for proposing an operation.

        In M3 nothing calls this but tests: the UI cannot construct an invocation by contract, and
        the planner that will is M4. It exists now so that when M4 arrives, the path from a proposed
        invocation to a human decision is already built and tested rather than being invented at the
        same time as the thing proposing them.
        """
        verdict = self.guard.evaluate(invocation)

        if isinstance(verdict, Pending):
            self.awaiting[verdict.request_id] = verdict
            await self.hub.broadcast(
                self.messages.approval_request(
                    request_id=verdict.request_id,
                    capability=verdict.capability.name,
                    resolved_args=verdict.resolved_args,
                    affected_paths=[str(path) for path in verdict.affected_paths],
                    side_effect=verdict.side_effect,
                    origin=verdict.origin,
                )
            )

        return verdict


def create_app(
    *,
    pipe_name: str = DEFAULT_PIPE_NAME,
    start_pipe_client: bool = True,
    log: Any = print,
    workspace: Path | None = None,
    trust_path: Path | None = None,
    audit_path: Path | None = None,
) -> FastAPI:
    """Builds the application.

    ``start_pipe_client`` exists so tests can drive the link directly instead of standing up a
    Windows pipe for every WebSocket case. The three path arguments exist for the same reason: a
    test gets a temporary workspace and its own trust file rather than reaching into the real one.
    """
    queue: asyncio.Queue[PipeEvent] = asyncio.Queue()
    counters = DropCounters()
    hub = UiHub(log=log)
    messages = WsMessageFactory()

    root = workspace or workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    trust = TrustStore(trust_path or TrustStore.default_path())

    guard = Guard(
        registry=build_registry(root),
        workspace=root,
        audit=AuditLog(audit_path or Path("logs") / "audit.jsonl"),
        trust=trust,
        # The trust file is out of reach for every capability whatever its allowed_roots says. It is
        # already outside the workspace, so containment refuses it today; this keeps that true when
        # M5 registers capabilities with roots wide enough to contain it.
        protected_paths=(trust.path,),
    )

    services = BrainServices(
        counters=counters,
        hub=hub,
        messages=messages,
        link=None,  # type: ignore[arg-type]
        guard=guard,
        trust=trust,
    )

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
        while True:
            frame = await websocket.receive_text()
            await _handle_ui_frame(websocket, services, frame)
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
        # Stricter than the inbound union on purpose: the *first* frame must be a hello, so a client
        # cannot open a connection with a decision for a request that predates it.
        CLIENT_HELLO_ADAPTER.validate_json(frame)
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

    # Immediately after the hello, because a tab that does not yet know approval is off would show
    # the safe state while the permissive one is in force - the wrong way round to be wrong. Sent as
    # its own message rather than folded into server.hello: additionalProperties is false, so adding
    # a field there would be a breaking contract change. See docs/CONTRACTS.md section 5.
    trust = services.trust.load()
    await websocket.send_json(services.messages.trust_status(enabled=trust.enabled, since=trust.since))

    return True


async def _handle_ui_frame(websocket: WebSocket, services: BrainServices, frame: str) -> None:
    """The UI holds no authority beyond answering what the brain already resolved.

    It can approve or reject a request the brain raised, and it can turn approval off - which is the
    user's own switch, not an invocation. It cannot construct, alter or re-scope an operation. The
    inbound adapter admits exactly those three message types; anything else is refused and counted
    rather than parsed for meaning.
    """
    try:
        message = CLIENT_MESSAGE_ADAPTER.validate_json(frame)
    except ValidationError as error:
        services.counters.record_schema_violation()
        await websocket.send_json(
            services.messages.error(
                code="schema_violation",
                message="The UI may not send this message. Only client.hello, approval.decision and "
                "trust.set are accepted on this socket.",
            )
        )
        _ = error
        return

    if isinstance(message, ApprovalDecision):
        await _apply_decision(services, message)
        return

    if isinstance(message, TrustSet):
        await _apply_trust(services, message)
        return

    # A second client.hello on an established connection.
    services.counters.record_schema_violation()
    await websocket.send_json(
        services.messages.error(
            code="schema_violation",
            message="This connection has already completed its handshake.",
        )
    )


async def _apply_decision(services: BrainServices, message: ApprovalDecision) -> None:
    """Settles one request, executes it or does not, and tells every tab the outcome."""
    request_id = message.payload.request_id
    approved = message.payload.decision == "approve"

    # The queue is what enforces one answer per request. A replayed decision - captured, retried by
    # a reconnecting client, or simply sent twice - finds nothing here and authorises nothing.
    settled = services.guard.queue.resolve(request_id, approved=approved)
    pending = services.awaiting.pop(request_id, None)

    if settled is None or pending is None:
        services.counters.record_schema_violation()
        return

    if approved:
        _execute(services, pending)
    else:
        # So the identical invocation is not offered again this session.
        services.guard.record_rejection(pending)
        services.guard.audit_decision(pending, decision="denied_user", reason="the user rejected it")

    await services.hub.broadcast(
        services.messages.approval_resolved(
            request_id=request_id,
            outcome="approved" if approved else "rejected",
        )
    )


def _execute(services: BrainServices, pending: Pending) -> None:
    """Runs an approved invocation, recording it before it runs.

    Audited first so a crash mid-operation still leaves a record - docs/SECURITY.md section 9. A
    handler that raises is recorded as having been allowed, because it was.
    """
    services.guard.audit_decision(pending, decision="allowed", reason="approved by the user")
    pending.capability.handler(**pending.resolved_args)


async def _apply_trust(services: BrainServices, message: TrustSet) -> None:
    """The only path that changes trust state.

    Reachable only from a UI frame. It is not a registered capability, so no invocation can arrive
    here, and the file it writes sits outside every capability's allowed_roots.
    """
    state = services.trust.set(enabled=message.payload.enabled)
    await services.hub.broadcast(services.messages.trust_status(enabled=state.enabled, since=state.since))


async def _close_with_error(websocket: WebSocket, services: BrainServices, *, code: Any, message: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json(services.messages.error(code=code, message=message))
        await websocket.close(code=_CLOSE_PROTOCOL_ERROR)


def _is_version_mismatch(error: ValidationError) -> bool:
    return any(item["loc"][-1:] == ("v",) for item in error.errors())


#: The importable application for `uvicorn local_zero_brain.ws.server:app`.
app = create_app()
