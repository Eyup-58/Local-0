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
from local_zero_brain.capabilities.guard import Denied, Guard, Invocation, Pending, Verdict
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.capabilities.paths import workspace_root
from local_zero_brain.capabilities.registry import CapabilityRegistry
from local_zero_brain.contracts.common import CONTRACT_VERSION
from local_zero_brain.contracts.ws import (
    CLIENT_HELLO_ADAPTER,
    CLIENT_MESSAGE_ADAPTER,
    ApprovalDecision,
    CredentialSet,
    MemoryReindex,
    ProviderSelect,
    TrustSet,
    TurnRequest,
)
from local_zero_brain.credentials import CredentialStore, Secret
from local_zero_brain.ipc.pipe_client import DEFAULT_PIPE_NAME, PipeEvent, SystemPipeClient
from local_zero_brain.link import SystemLink
from local_zero_brain.metrics import DropCounters
from local_zero_brain.answerer import Answerer
from local_zero_brain.planner import Planner, Proposal
from local_zero_brain.llm.provider import MissingKey, Provider, ProviderError, build_provider
from local_zero_brain.llm.ollama import DEFAULT_MODEL as LOCAL_MODEL, OllamaProvider
from local_zero_brain.llm.gemini import DEFAULT_MODEL as CLOUD_MODEL
from local_zero_brain.memory.index import MemoryIndex
from local_zero_brain.memory.manager import MemoryManager
from local_zero_brain.memory.vault import TRUSTED_FOLDERS
from local_zero_brain.net.egress import EgressGuard
from local_zero_brain.providers import ProviderStore
from local_zero_brain.trust import TrustStore
from local_zero_brain.ws.hub import UiHub
from local_zero_brain.ws.messages import WsMessageFactory, utc_now

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
    #: The network boundary. Local until the user selects otherwise.
    egress: EgressGuard
    #: Which model layer is selected, persisted across restarts.
    providers: ProviderStore
    #: Where the cloud key lives. The brain reads it to answer "is one stored"; it never sends it.
    credentials: CredentialStore
    #: Long-term memory over the Obsidian vault. Disabled, not broken, when there is no vault.
    memory: MemoryManager
    #: What the model is told exists. Shared with the guard so the two cannot drift apart; it is
    #: still not the whitelist - the guard's step 1 is, and it runs again on whatever comes back.
    registry: CapabilityRegistry
    log: Any = print
    #: Guard verdicts waiting on a human, by request_id.
    #:
    #: Held here rather than in the queue because executing an approved invocation needs the
    #: Capability object and its resolved arguments, and the queue deliberately stores only what the
    #: user was shown. The queue remains the thing that enforces one-answer-per-request.
    awaiting: dict[str, Pending] = field(default_factory=dict)
    client: SystemPipeClient | None = None
    reader: asyncio.Task[None] | None = None

    def provider(self) -> Provider:
        """The model layer for right now, read from the persisted mode every time it is asked for.

        **Deliberately not cached, and not chosen at construction.** The boundary moves at runtime:
        a component holding the provider it was built with would keep calling the cloud after the
        user selected Local, and the UI would report one boundary while another was in force - the
        wrong way round to be wrong, and the same failure `trust.status` and `provider.status` are
        carried as their own messages to avoid. Until this existed the planner held an
        ``OllamaProvider`` from startup, so selecting Cloud opened the egress guard and changed
        nothing else: exposure with no benefit.

        ``build_provider`` raises ``MissingKey`` when Cloud is selected with nothing stored.
        ``_apply_provider`` refuses to *enter* Cloud without a key, so reaching that means the key
        went away underneath a selection that was valid when it was made - a state to report, not to
        paper over by falling back to Local while the UI still says Cloud.
        """
        state = self.providers.load()

        return build_provider(
            mode=state.mode,
            # Read per call for the same reason the mode is: a key entered or deleted mid-session
            # must not need a restart to take effect.
            key=self.credentials.read() if state.mode == "cloud" else None,
        )

    def planner(self) -> Planner:
        """A planner bound to whichever provider is live for this turn.

        Rebuilt per turn rather than held as a field, which is what makes a stale provider
        impossible rather than merely unlikely. Construction is two attribute assignments.
        """
        return Planner(provider=self.provider(), registry=self.registry)

    def answerer(self) -> Answerer:
        """An answerer on the same provider. Rebuilt per turn, for the same reason.

        Note what is *not* passed: the registry. The answerer builds an empty one and there is no
        argument that could give it another, which is the property that lets its output go straight
        into a caption without going anywhere near the guard.
        """
        return Answerer(provider=self.provider())

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
    provider_path: Path | None = None,
    credential_target: str | None = None,
    memory_path: Path | None = None,
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
    providers = ProviderStore(provider_path or ProviderStore.default_path())
    audit = AuditLog(audit_path or Path("logs") / "audit.jsonl")

    # The embedding provider is always the local one, whatever mode the user has selected. That is
    # docs/SECURITY.md section 11's "embeddings are local in both modes" made structural rather than
    # remembered: indexing the vault through a network provider would send its contents off the
    # machine one chunk at a time, and there is no code path here that could.
    memory = MemoryManager.from_environment(
        index_path=memory_path or MemoryIndex.default_path(),
        provider=OllamaProvider(),
        log=log,
    )

    # The trusted half of the vault is unreachable to every capability, whatever its allowed_roots
    # says. A capability that could write there could author the user's own memories, and they would
    # come back next session as text the planner is allowed to act on.
    protected_memory = tuple(memory.root / folder for folder in TRUSTED_FOLDERS) if memory.root else ()

    # Built once and shared with the planner below: what the model is told exists and what the guard
    # will accept are then the same list by construction, rather than two places kept in step.
    registry = build_registry(root, vault=memory.root)

    guard = Guard(
        registry=registry,
        workspace=root,
        audit=audit,
        trust=trust,
        # Local Zero's own control files are out of reach for every capability whatever its
        # allowed_roots says. Both are already outside the workspace, so containment refuses them
        # today; this keeps that true when M5 registers capabilities with roots wide enough to
        # contain them. provider.json belongs here for the same reason trust.json does: a capability
        # that could write it could move the egress boundary into Cloud mode, and the boundary would
        # then be something the system can open for itself.
        protected_paths=(trust.path, providers.path, memory.index.path, *protected_memory),
    )

    services = BrainServices(
        counters=counters,
        hub=hub,
        messages=messages,
        link=None,  # type: ignore[arg-type]
        guard=guard,
        trust=trust,
        egress=EgressGuard(audit=audit, mode=providers.load().mode),
        providers=providers,
        credentials=CredentialStore(credential_target) if credential_target else CredentialStore(),
        memory=memory,
        registry=registry,
        log=log,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Before anything else can open a socket. docs/SECURITY.md section 11: Local mode is the
        # state before anybody chooses anything, and in Local mode the guard is total - nothing
        # non-loopback connects, whatever library attempts it.
        services.egress.install()

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

        # On a worker thread: a first scan walks the whole vault and would otherwise hold the event
        # loop - and with it the telemetry stream - for as long as that takes. Memory is the part of
        # this product that may be slow; the panel is not.
        indexer = asyncio.create_task(asyncio.to_thread(services.memory.reindex), name="memory-index")

        try:
            yield
        finally:
            services.reader.cancel()
            indexer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await services.reader
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await indexer
            if client is not None:
                client.stop()

            services.egress.uninstall()

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

    # For the same reason as trust: a tab that does not yet know the boundary is open would show the
    # safe state while the permissive one is in force.
    await websocket.send_json(_provider_frame(services))
    await websocket.send_json(_memory_frame(services))

    # Last of the state frames. Idle with no caption, because at connect nothing is running and the
    # brain keeps no record of a turn that began before this socket existed. Sent explicitly rather
    # than left to a UI default: the tab is told what the state is, it does not assume one.
    await websocket.send_json(services.messages.turn_state(state="idle", since=utc_now()))

    return True


def _memory_frame(services: BrainServices) -> dict[str, Any]:
    status = services.memory.status()

    return services.messages.memory_status(
        enabled=status.enabled,
        vault=status.vault,
        notes=status.notes,
        chunks=status.chunks,
        embedded_chunks=status.embedded_chunks,
        last_indexed_at=status.last_indexed_at,
        embeddings_available=status.embeddings_available,
    )


def _provider_frame(services: BrainServices) -> dict[str, Any]:
    """The current selection, as the UI is allowed to see it.

    ``has_key`` is read from the Credential Manager each time rather than cached, because the user
    can remove the entry in Windows' own interface and a cached true would leave the UI offering a
    mode that cannot authenticate.
    """
    state = services.providers.load()
    return services.messages.provider_status(
        mode=state.mode,
        model=LOCAL_MODEL if state.mode == "local" else CLOUD_MODEL,
        has_key=services.credentials.has_key(),
        since=state.since,
    )


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
                message="The UI may not send this message. Only client.hello, approval.decision, "
                "trust.set, provider.select, credential.set and memory.reindex are accepted on "
                "this socket.",
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

    if isinstance(message, ProviderSelect):
        await _apply_provider(websocket, services, message)
        return

    if isinstance(message, CredentialSet):
        await _apply_credential(services, message)
        return

    if isinstance(message, MemoryReindex):
        await _rescan_memory(services)
        return

    if isinstance(message, TurnRequest):
        await _plan(services, message)
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
        await _execute(services, pending)
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


async def _execute(services: BrainServices, pending: Pending) -> None:
    """Runs an approved invocation, recording it before it runs.

    Audited first so a crash mid-operation still leaves a record - docs/SECURITY.md section 9. A
    handler that raises is still recorded as having been allowed, because it was; what changes is
    that the user is told it did not work.

    Run on a worker thread. Handlers do synchronous file I/O, and doing that on the event loop would
    stall every socket the brain holds - including the telemetry the user is watching - for as long
    as the write takes.
    """
    services.guard.audit_decision(pending, decision="allowed", reason="approved by the user")

    name = pending.capability.name

    # The run is announced before it starts and closed out after. These frames are the only way the
    # UI learns a capability ran: it infers nothing from elapsed time, so a run the brain does not
    # report is a run the panel does not draw. Announcing the start separately is what lets a slow
    # handler show as running rather than as nothing at all until it finishes.
    started = utc_now()
    await services.hub.broadcast(
        services.messages.turn_state(state="tool_running", since=started, detail=name)
    )
    await services.hub.broadcast(
        services.messages.tool_log(at=started, capability=name, message="Started.", status="running")
    )

    try:
        await asyncio.to_thread(pending.capability.handler, **pending.resolved_args)
    except Exception as error:  # noqa: BLE001 - a handler may raise anything; none of it may escape
        # An operation the user approved and which then failed is exactly the case this product must
        # not be silent about. Letting it propagate would tear down the socket and the UI would
        # reconnect showing nothing wrong, which is the failure mode the whole panel is built to
        # avoid. The class name, not the message: an exception's text can carry a path.
        services.log(f"capability {name} failed: {type(error).__name__}")
        failed_at = utc_now()
        await services.hub.broadcast(
            services.messages.tool_log(
                at=failed_at,
                capability=name,
                message=f"Failed with {type(error).__name__}.",
                status="failed",
            )
        )
        await services.hub.broadcast(services.messages.turn_state(state="idle", since=failed_at))
        await services.hub.broadcast(
            services.messages.error(
                code="internal_error",
                message=f"{name} was approved but did not complete. "
                f"It failed with {type(error).__name__}. Nothing further was attempted.",
            )
        )
        return

    finished = utc_now()
    await services.hub.broadcast(
        services.messages.tool_log(at=finished, capability=name, message="Completed.", status="ok")
    )
    await services.hub.broadcast(services.messages.turn_state(state="idle", since=finished))


def _turn(services: BrainServices, text: str) -> tuple[Proposal, str | None]:
    """Recall once, then propose - and answer from the same notes when nothing was proposed.

    Runs on a worker thread; every part of it blocks. Returns the proposal and, when the planner
    named no capability, the answer to put in the caption.

    **The vault goes to whichever provider is selected, including the cloud one.** That is a decision
    taken deliberately and it is the sharpest edge in this file: in Cloud mode the chunks recalled
    here are the user's own notes, and they leave the machine inside the prompt. docs/SECURITY.md
    section 11 states it, and the UI warns before the switch rather than after - a capability the
    user did not know they had enabled is not a feature.

    Only ``recall_trusted`` is called, so what travels is notes the user wrote themselves.
    Agent-written memory is ``UntrustedChunk`` and has no conversion to the type either the planner
    or the answerer accepts.

    The recall happens **once** and both components see the same notes. Recalling twice would let
    the answer be drawn from different notes than the proposal was judged against, and a user asking
    "why did it do that" would be reading an explanation of a decision that was never made.
    """
    notes = services.memory.recall_trusted(text)
    proposal = planner_result = services.planner().propose(text, context=notes)

    if planner_result.invocation is not None:
        return proposal, None

    # Nothing to do, so answer instead. The answerer holds an empty registry by construction, so
    # this branch cannot reach an executor however persuasive the notes are - what comes back is
    # prose, and the only thing done with it is putting it in a caption.
    return proposal, services.answerer().answer(text, notes)


async def _plan(services: BrainServices, message: TurnRequest) -> None:
    """One conversational turn: think, then either propose something or say why not.

    The turn is *reported* at each step rather than inferred by the UI from elapsed time, which is
    the whole reason turn.state exists. Three outcomes, and none of them invents prose:

    * The model names a capability. It goes through ``invoke()`` into the same five-step guard chain
      as everything else, and the turn returns to idle - the approval dialog is already on screen
      saying what is waiting, and a caption narrating over it would be the panel talking about a
      decision the user is in the middle of reading.
    * The model declines. Its own reason becomes the caption and the turn is `speaking`. Without a
      reason the caption is null, which renders as nothing: a stand-in sentence would be the panel
      putting words in the brain's mouth.
    * The model or the provider fails. The user is told, in the panel's voice, and the turn ends.

    ``speaking`` is held rather than followed by an idle: there is no TTS here, so nothing marks an
    utterance as finished, and dropping straight back to idle would blank the words the moment they
    arrived. The next request moves it on.
    """
    text = message.payload.text

    await services.hub.broadcast(services.messages.turn_state(state="thinking", since=utc_now()))

    try:
        # Resolved before the worker thread so a missing key is reported as itself rather than as a
        # generic planning failure.
        services.provider()
    except MissingKey as error:
        # Its own branch because the message is worth passing through verbatim: build_provider's
        # wording tells the user what to do about it, and "failed with MissingKey" would not.
        services.log("planning refused: cloud mode with no key")
        await services.hub.broadcast(services.messages.turn_state(state="idle", since=utc_now()))
        await services.hub.broadcast(
            services.messages.error(code="provider_unavailable", message=str(error))
        )
        return

    try:
        # Off the event loop: the recall, the proposal and the answer all block, and holding the loop
        # for the length of them would stall every socket the brain has - including the telemetry the
        # user is watching while they wait.
        proposal, answer = await asyncio.to_thread(_turn, services, text)
    except ProviderError as error:
        # ProviderError's own text is built from a status code and a fixed table - it never contains
        # a response body, a URL or a key - so it is passed through. It is the difference between
        # "quota reached, wait or switch to Local" and "it failed with ProviderError", and the user
        # can act on exactly one of those.
        services.log(f"planning failed: {type(error).__name__}")
        await services.hub.broadcast(services.messages.turn_state(state="idle", since=utc_now()))
        await services.hub.broadcast(
            services.messages.error(
                code="provider_unavailable",
                message=f"Nothing was proposed and nothing ran: {error}.",
            )
        )
        return
    except Exception as error:  # noqa: BLE001 - a provider may raise anything; none of it may escape
        services.log(f"planning failed: {type(error).__name__}")
        await services.hub.broadcast(services.messages.turn_state(state="idle", since=utc_now()))
        await services.hub.broadcast(
            services.messages.error(
                code="provider_unavailable",
                # The class name, not the message: an arbitrary exception's text can carry a path.
                message=f"The model layer did not answer. It failed with {type(error).__name__}. "
                f"Nothing was proposed and nothing ran.",
            )
        )
        return

    if proposal.invocation is None:
        # The answer, not the planner's reason. "No listed capability fits" is true and useless: it
        # explains why nothing ran, to a user who asked a question rather than for an operation. The
        # reason stays in the log for the case where the answerer itself had nothing to say.
        if answer is None or not answer.strip():
            services.log(f"planner declined with no answer: {proposal.reason!r}")
        await services.hub.broadcast(
            services.messages.turn_state(state="speaking", since=utc_now(), caption=answer)
        )
        return

    verdict = await services.invoke(proposal.invocation)

    # Back to idle whatever the verdict. If it is Pending the approval dialog is already up; if it
    # was allowed outright, _execute has its own tool_running -> idle to report.
    await services.hub.broadcast(services.messages.turn_state(state="idle", since=utc_now()))

    if isinstance(verdict, Denied):
        await services.hub.broadcast(
            services.messages.tool_log(
                at=utc_now(),
                capability=proposal.invocation.capability,
                message=f"Refused at {verdict.step}.",
                status="failed",
            )
        )


async def _apply_trust(services: BrainServices, message: TrustSet) -> None:
    """The only path that changes trust state.

    Reachable only from a UI frame. It is not a registered capability, so no invocation can arrive
    here, and the file it writes sits outside every capability's allowed_roots.
    """
    state = services.trust.set(enabled=message.payload.enabled)
    await services.hub.broadcast(services.messages.trust_status(enabled=state.enabled, since=state.since))


async def _apply_provider(websocket: WebSocket, services: BrainServices, message: ProviderSelect) -> None:
    """Moves the network boundary, or refuses to.

    Selecting Cloud with no key stored is refused rather than accepted: the egress guard would be
    open while nothing could authenticate, which is the worst of both states - outbound permitted,
    no working provider, and a UI that says Cloud.
    """
    mode = message.payload.mode

    if mode == "cloud" and not services.credentials.has_key():
        await websocket.send_json(
            services.messages.error(
                code="provider_unavailable",
                message="Cloud mode needs a key and none is stored. Enter one first; it is written "
                "to the Windows Credential Manager. The boundary stays local until then.",
            )
        )
        return

    state = services.providers.set(mode=mode)
    # The persisted selection and the live guard move together. If these could disagree, the UI
    # would be reporting one boundary while another was in force.
    services.egress.set_mode(state.mode)
    await services.hub.broadcast(_provider_frame(services))


async def _apply_credential(services: BrainServices, message: CredentialSet) -> None:
    """Writes the key straight into the Windows Credential Manager.

    Nothing about this frame is logged, audited or echoed - not on success, not on failure. The
    acknowledgement is a provider.status carrying has_key, which is what the UI needs and all it
    gets. docs/CONTRACTS.md section 5.
    """
    services.credentials.write(Secret(message.payload.key))
    await services.hub.broadcast(_provider_frame(services))


async def _rescan_memory(services: BrainServices) -> None:
    """Rescans the configured vault and tells every tab the new counts.

    On a worker thread: a scan walks the vault and embeds what changed, and doing that on the event
    loop would stall the telemetry stream for as long as it takes. Memory is the part of this
    product that may be slow; the panel is not.
    """
    await asyncio.to_thread(services.memory.reindex)
    await services.hub.broadcast(_memory_frame(services))


async def _close_with_error(websocket: WebSocket, services: BrainServices, *, code: Any, message: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json(services.messages.error(code=code, message=message))
        await websocket.close(code=_CLOSE_PROTOCOL_ERROR)


def _is_version_mismatch(error: ValidationError) -> bool:
    return any(item["loc"][-1:] == ("v",) for item in error.errors())


#: The importable application for `uvicorn local_zero_brain.ws.server:app`.
app = create_app()
