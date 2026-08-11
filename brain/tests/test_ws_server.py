"""The WebSocket endpoint the UI connects to.

Driven in-process through Starlette's TestClient. The pipe client is left unstarted: these cases
are about what the socket does, and the link is fed directly where a system layer is needed.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import ipc_hello
from local_zero_brain.contracts.ws import WS_MESSAGE_ADAPTER
from local_zero_brain.ipc.pipe_client import PipeConnected, PipeLine
from local_zero_brain.ws.server import BIND_HOST, create_app

CLIENT_HELLO = {
    "v": 1,
    "id": "1f2e3d4c-5b6a-4978-8867-56453423120f",
    "ts": "2026-08-11T09:14:02.117Z",
    "type": "client.hello",
    "payload": {"component": "ui", "app_version": "0.1.0"},
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(start_pipe_client=False, log=lambda _: None))


def test_the_brain_binds_to_loopback_only() -> None:
    """A local assistant that starts listening on a LAN interface is a different product with a
    different threat model."""
    assert BIND_HOST == "127.0.0.1"


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


def test_a_frame_the_ui_may_not_send_is_refused_and_counted(client: TestClient) -> None:
    """The UI holds no authority. It cannot construct anything the brain has not already resolved,
    and in M1 it has nothing at all to send after its hello."""
    with client, client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps(CLIENT_HELLO))
        socket.receive_json()

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
        socket.send_text(json.dumps(CLIENT_HELLO))
        assert socket.receive_json()["type"] == "server.hello"

        services = client.app.state.services
        services.link._queue.put_nowait(PipeConnected())
        services.link._queue.put_nowait(PipeLine(json.dumps(ipc_hello())))

        frame = socket.receive_json()

    assert frame["type"] == "system.status"
    assert frame["payload"]["connected"] is True


def test_a_disconnecting_client_is_unregistered(client: TestClient) -> None:
    with client:
        with client.websocket_connect("/ws") as socket:
            socket.send_text(json.dumps(CLIENT_HELLO))
            socket.receive_json()
            assert client.app.state.services.hub.client_count == 1

        # Leaving the with-block closes the socket; the endpoint's finally clause unregisters it.
        assert client.app.state.services.hub.client_count == 0
