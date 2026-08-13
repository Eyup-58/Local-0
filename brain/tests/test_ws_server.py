"""The WebSocket endpoint the UI connects to.

Driven in-process through Starlette's TestClient. The pipe client is left unstarted: these cases
are about what the socket does, and the link is fed directly where a system layer is needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import ipc_hello
from local_zero_brain.audit import AuditLog
from local_zero_brain.capabilities.guard import Guard, Invocation, Pending
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.capabilities.registry import CapabilityRegistry
from local_zero_brain.contracts.ws import WS_MESSAGE_ADAPTER
from local_zero_brain.ipc.pipe_client import PipeConnected, PipeLine
from local_zero_brain.credentials import CredentialStore
from local_zero_brain.memory.index import MemoryIndex
from local_zero_brain.memory.manager import MemoryManager
from local_zero_brain.metrics import DropCounters
from local_zero_brain.net.egress import EgressGuard
from local_zero_brain.providers import ProviderStore
from local_zero_brain.trust import TrustStore
from local_zero_brain.ws.messages import WsMessageFactory
from local_zero_brain.ws.server import BIND_HOST, BrainServices, _execute, create_app

CLIENT_HELLO = {
    "v": 1,
    "id": "1f2e3d4c-5b6a-4978-8867-56453423120f",
    "ts": "2026-08-11T09:14:02.117Z",
    "type": "client.hello",
    "payload": {"component": "ui", "app_version": "0.1.0"},
}


def isolated_app(tmp_path: Path, **overrides: object):
    """An app whose workspace, trust file, audit log and memory index are all temporary.

    Without these paths, create_app falls back to the real %LOCALAPPDATA%\\LocalZero and a relative
    logs/ directory - so the suite would read the user's actual trust state, meaning these tests
    would start failing the day somebody turned the button on, would scatter audit lines into
    whatever directory pytest happened to run from, and would write a memory index into the user's
    own profile.
    """
    defaults: dict[str, object] = {
        "start_pipe_client": False,
        "log": lambda _: None,
        "workspace": tmp_path / "workspace",
        "trust_path": tmp_path / "trust.json",
        "audit_path": tmp_path / "logs" / "audit.jsonl",
        "memory_path": tmp_path / "memory.sqlite",
    }
    return create_app(**{**defaults, **overrides})


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(isolated_app(tmp_path))


def test_the_brain_binds_to_loopback_only() -> None:
    """A local assistant that starts listening on a LAN interface is a different product with a
    different threat model."""
    assert BIND_HOST == "127.0.0.1"


def test_the_built_ui_is_served_from_the_same_origin_as_the_socket(tmp_path: Path) -> None:
    """One origin is what lets the client say ``ws://{location.host}/ws`` instead of a port literal.

    Serving the UI from anywhere else puts the port back into two places, which is the M7 gap.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Local Zero</title>", encoding="utf-8")

    with TestClient(isolated_app(tmp_path, ui_dist=dist)) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "Local Zero" in page.text


def test_an_unbuilt_ui_names_the_command_and_leaves_the_socket_working(tmp_path: Path) -> None:
    """A missing build is a stated fact, not a crash. Everything but the page still works."""
    lines: list[str] = []

    app = isolated_app(tmp_path, ui_dist=tmp_path / "never-built", log=lines.append)
    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        reply = socket.receive_json()

    assert reply["type"] == "server.hello"
    assert any("npm run build" in line for line in lines)


def test_the_static_mount_does_not_serve_anything_outside_the_build(tmp_path: Path) -> None:
    """The one new filesystem surface the package adds. A traversal out of dist is refused."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the browser", encoding="utf-8")

    with TestClient(isolated_app(tmp_path, ui_dist=dist)) as client:
        escaped = client.get("/../secret.txt")

    assert escaped.status_code != 200
    assert "not for the browser" not in escaped.text


def test_a_valid_hello_is_answered_with_server_hello(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        reply = socket.receive_json()

    assert reply["type"] == "server.hello"
    assert reply["payload"]["component"] == "brain"


def test_the_first_reply_says_the_system_layer_is_not_connected(client: TestClient) -> None:
    """An honest starting state. The UI shows a starting-up state rather than an empty panel that
    looks like zero sensors exist."""
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        reply = socket.receive_json()

    assert reply["payload"]["system_connected"] is False
    assert reply["payload"]["sensors"] == []


def test_every_frame_the_brain_sends_validates_against_the_ws_contract(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        reply = socket.receive_json()

    WS_MESSAGE_ADAPTER.validate_python(reply)


def test_a_malformed_hello_is_refused_and_the_socket_closed(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text("this is not a message")
        reply = socket.receive_json()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "schema_violation"


def test_a_hello_with_an_unimplemented_version_is_refused_as_such(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps({**CLIENT_HELLO, "v": 99}))
        reply = socket.receive_json()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "unsupported_version"


def handshake(socket) -> None:
    """Completes the opening exchange: client.hello, then every state frame the brain opens with.

    The state messages arrive immediately after the hello for the same reason: a tab that does not
    yet know approval is off - or that the network boundary is open - would show the safe state while
    the permissive one is in force, which is the wrong way round to be wrong. turn.state is here on
    the same principle: the tab is told what is running rather than assuming a default.
    """
    socket.send_text(json.dumps(CLIENT_HELLO))
    assert socket.receive_json()["type"] == "server.hello"
    assert socket.receive_json()["type"] == "trust.status"
    assert socket.receive_json()["type"] == "provider.status"
    assert socket.receive_json()["type"] == "memory.status"
    assert socket.receive_json()["type"] == "turn.state"


def test_a_corrupt_memory_index_does_not_take_the_connection_down(
    client: TestClient, tmp_path: Path
) -> None:
    """The handshake calls ``status()``, so a broken cache used to cost the whole socket.

    Memory is one feature. Telemetry and approval do not depend on it, and the frame that reports
    the turn state is sent *after* the memory frame - so an exception here meant the tab never
    finished connecting and reconnected into the same failure. The index is created lazily, so
    writing the file after the app is built is what a corrupt cache from a previous run looks like.
    """
    (tmp_path / "memory.sqlite").write_bytes(b"this is not a sqlite database" * 40)

    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)


async def test_an_approved_operation_that_fails_tells_the_user(tmp_path: Path) -> None:
    """An operation the user approved and which then failed is exactly what this product must not be
    silent about.

    Before this was guarded, a handler raising propagated into the socket handler: the connection
    died, the UI reconnected showing nothing wrong, and the audit said the operation was allowed.
    That is the failure mode the whole panel exists to avoid.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    guard = Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
    )

    # Passes every guard step and then fails in the handler: the file is not there to delete.
    verdict = guard.evaluate(Invocation("delete_file", {"path": str(workspace / "never-existed.txt")}))
    assert isinstance(verdict, Pending)

    sent: list[dict] = []

    class RecordingHub:
        async def broadcast(self, message: dict) -> None:
            sent.append(message)

    services = BrainServices(
        counters=DropCounters(),
        hub=RecordingHub(),  # type: ignore[arg-type]
        link=None,  # type: ignore[arg-type]
        messages=WsMessageFactory(),
        guard=guard,
        trust=TrustStore(tmp_path / "trust.json"),
        egress=EgressGuard(audit=AuditLog(tmp_path / "logs" / "audit.jsonl")),
        providers=ProviderStore(tmp_path / "provider.json"),
        credentials=CredentialStore(target="LocalZero/test/ws-server"),
        memory=MemoryManager(root=None, index=MemoryIndex(tmp_path / "memory.sqlite")),
        # Empty on purpose: this test drives _execute directly, past the point the planner has
        # anything to do. Handing it the real registry would leave what the model is offered
        # looking like part of this test's setup when it is not.
        registry=CapabilityRegistry(()),
        log=lambda _: None,
    )

    # Must not raise: the failure is reported, not propagated.
    #
    # Called with the capability and its resolved arguments rather than the verdict: `_execute`
    # serves both ways in, and only the approval path audits - the guard already recorded an
    # `Allowed` when it allowed it.
    await _execute(services, verdict.capability, verdict.resolved_args)

    # The run is reported as it happens: announced, logged as running, then closed out as failed and
    # returned to idle before the error itself. The UI infers none of this from elapsed time.
    assert [frame["type"] for frame in sent] == [
        "turn.state",
        "tool.log",
        "tool.log",
        "turn.state",
        "error",
    ]
    assert sent[0]["payload"]["state"] == "tool_running"
    assert sent[1]["payload"]["status"] == "running"
    # A failed handler is reported as failed. Rounding it up to ok would paint the row green.
    assert sent[2]["payload"]["status"] == "failed"
    assert sent[3]["payload"]["state"] == "idle"

    error = sent[-1]
    assert "did not complete" in error["payload"]["message"]
    # The class name reaches the user; the exception's text does not, because it can carry a path.
    assert str(workspace) not in error["payload"]["message"]
    # And it does not reach the log line either, which is the other place a path could leak.
    assert all(str(workspace) not in json.dumps(frame) for frame in sent)


def test_trust_state_is_reported_immediately_after_the_handshake(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        assert socket.receive_json()["type"] == "server.hello"

        trust = socket.receive_json()

    assert trust["type"] == "trust.status"
    # Off unless the user turned it on. A fresh install has approval in force.
    assert trust["payload"]["enabled"] is False


def test_a_frame_the_ui_may_not_send_is_refused_and_counted(client: TestClient) -> None:
    """The UI's authority is bounded: it may answer a request the brain raised and set its own trust
    switch, and nothing else. A server.hello arriving inbound is not a message it may send."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)

        socket.send_text(json.dumps({**CLIENT_HELLO, "type": "server.hello"}))
        reply = socket.receive_json()

        counters = client.app.state.services.counters.snapshot()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "schema_violation"
    assert counters.schema_violations == 1


def test_nothing_is_streamed_before_the_client_hello(client: TestClient) -> None:
    """The brain streams nothing to an unregistered socket, so a telemetry sample produced while
    the UI has not handshaked reaches nobody."""
    with client, client.websocket_connect("/ws") as socket:
        services = client.app.state.services
        services.link._session = None  # a fresh connection is about to be announced

        # Drive the link as if the sidecar had just connected and handshaked.
        portal_queue = services.link._queue
        portal_queue.put_nowait(PipeConnected())
        portal_queue.put_nowait(PipeLine(json.dumps(ipc_hello())))

        socket.send_text(json.dumps(CLIENT_HELLO))
        reply = socket.receive_json()

    # The first thing this socket ever receives is its server.hello, never a stray earlier frame.
    assert reply["type"] == "server.hello"


def test_a_registered_client_receives_broadcast_frames(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)

        services = client.app.state.services
        services.link._queue.put_nowait(PipeConnected())
        services.link._queue.put_nowait(PipeLine(json.dumps(ipc_hello())))

        frame = socket.receive_json()

    assert frame["type"] == "system.status"
    assert frame["payload"]["connected"] is True


def test_a_disconnecting_client_is_unregistered(client: TestClient) -> None:
    with client:
        with client.websocket_connect("/ws") as socket:
            # The full opening exchange, not just the first frame: registration happens after the
            # brain has sent all of it, so reading one frame and asserting is a race.
            handshake(socket)
            assert client.app.state.services.hub.client_count == 1

        # Leaving the with-block closes the socket; the endpoint's finally clause unregisters it.
        assert client.app.state.services.hub.client_count == 0
