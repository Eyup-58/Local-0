"""Selecting the network boundary from the UI, and getting a key into Credential Manager.

Three properties are being held here, and only the first is about features:

* the UI can move the boundary, and the persisted selection and the live egress guard move
  **together** - if those could disagree, the panel would report one boundary while another was in
  force;
* selecting Cloud with no key is **refused**, rather than accepted into a state where outbound is
  permitted and nothing can authenticate;
* the key **never comes back out**. Not in the acknowledgement, not in the audit log, not in an
  error message. That is asserted by searching the actual bytes rather than by reading the code.

The tests use a namespaced Credential Manager target so a run cannot collide with the real entry,
and delete it afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_zero_brain.credentials import CredentialStore
from local_zero_brain.ws.server import create_app

CLIENT_HELLO = {
    "v": 1,
    "id": "1f2e3d4c-5b6a-4978-8867-56453423120f",
    "ts": "2026-08-12T09:14:02.117Z",
    "type": "client.hello",
    "payload": {"component": "ui", "app_version": "0.1.0"},
}

#: Not shaped like a vendor key on purpose - see the note in test_credentials.py.
KEY = "local-zero-test-value-1111111111111111"

TEST_TARGET = "LocalZero/test/provider-ws"


def envelope(message_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "v": 1,
            "id": "2a3b4c5d-6e7f-4081-9203-a4b5c6d7e8f9",
            "ts": "2026-08-12T09:15:00.000Z",
            "type": message_type,
            "payload": payload,
        }
    )


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def client(tmp_path: Path, audit_path: Path) -> Iterator[TestClient]:
    store = CredentialStore(target=TEST_TARGET)
    store.delete()

    yield TestClient(
        create_app(
            start_pipe_client=False,
            log=lambda _: None,
            workspace=tmp_path / "workspace",
            trust_path=tmp_path / "trust.json",
            audit_path=audit_path,
            provider_path=tmp_path / "provider.json",
            credential_target=TEST_TARGET,
            memory_path=tmp_path / "memory.sqlite",
        )
    )

    store.delete()


#: server.hello, then the three state messages the UI must not be wrong about: whether approval is
#: on, where the network boundary is, and whether memory is loaded.
OPENING_FRAMES = 4


def handshake(socket) -> list[dict]:
    """Completes the handshake and returns the frames the brain opens with."""
    socket.send_text(json.dumps(CLIENT_HELLO))
    return [socket.receive_json() for _ in range(OPENING_FRAMES)]


def test_the_handshake_reports_the_boundary_before_anything_else_happens(client: TestClient) -> None:
    """For the same reason trust.status is sent there: a tab that does not yet know the boundary is
    open would show the safe state while the permissive one was in force."""
    with client, client.websocket_connect("/ws") as socket:
        frames = handshake(socket)

    assert [frame["type"] for frame in frames] == [
        "server.hello",
        "trust.status",
        "provider.status",
        "memory.status",
    ]
    assert frames[2]["payload"]["mode"] == "local"
    assert frames[2]["payload"]["has_key"] is False


def test_selecting_cloud_without_a_key_is_refused(client: TestClient) -> None:
    """Accepting it would open the egress guard while nothing could authenticate - outbound
    permitted, no working provider, and a UI saying Cloud."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("provider.select", {"mode": "cloud"}))
        reply = socket.receive_json()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "provider_unavailable"


def test_a_refused_selection_leaves_the_boundary_where_it_was(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("provider.select", {"mode": "cloud"}))
        socket.receive_json()

    assert client.app.state.services.egress.mode == "local"
    assert client.app.state.services.providers.load().mode == "local"


def test_storing_a_key_is_acknowledged_with_a_boolean_and_nothing_else(client: TestClient) -> None:
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("credential.set", {"key": KEY}))
        reply = socket.receive_json()

    assert reply["type"] == "provider.status"
    assert reply["payload"]["has_key"] is True
    assert KEY not in json.dumps(reply)


def test_the_key_never_reaches_the_audit_log(client: TestClient, audit_path: Path) -> None:
    """The audit log is the file people paste into an issue when something goes wrong."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("credential.set", {"key": KEY}))
        socket.receive_json()
        socket.send_text(envelope("provider.select", {"mode": "cloud"}))
        socket.receive_json()

    written = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    assert KEY not in written


def test_a_stored_key_lets_cloud_be_selected_and_moves_the_live_guard(client: TestClient) -> None:
    """The persisted selection and the running guard move together, or the UI is reporting a
    boundary that is not the one in force."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("credential.set", {"key": KEY}))
        socket.receive_json()

        socket.send_text(envelope("provider.select", {"mode": "cloud"}))
        reply = socket.receive_json()

        assert reply["type"] == "provider.status"
        assert reply["payload"]["mode"] == "cloud"
        assert client.app.state.services.egress.mode == "cloud"
        assert client.app.state.services.providers.load().mode == "cloud"


def test_the_selection_survives_a_restart(tmp_path: Path) -> None:
    """The boundary is a setting, not a session state. It is also the setting most worth being sure
    about after a restart, which is why it is asserted rather than assumed."""
    store = CredentialStore(target=TEST_TARGET)
    from local_zero_brain.credentials import Secret

    store.write(Secret(KEY))
    provider_path = tmp_path / "provider.json"

    def build() -> TestClient:
        return TestClient(
            create_app(
                start_pipe_client=False,
                log=lambda _: None,
                workspace=tmp_path / "workspace",
                trust_path=tmp_path / "trust.json",
                audit_path=tmp_path / "logs" / "audit.jsonl",
                provider_path=provider_path,
                credential_target=TEST_TARGET,
                memory_path=tmp_path / "memory.sqlite",
            )
        )

    try:
        first = build()
        with first, first.websocket_connect("/ws") as socket:
            handshake(socket)
            socket.send_text(envelope("provider.select", {"mode": "cloud"}))
            socket.receive_json()

        second = build()
        with second, second.websocket_connect("/ws") as socket:
            frames = handshake(socket)

        assert frames[2]["payload"]["mode"] == "cloud"
        assert second.app.state.services.egress.mode == "cloud"
    finally:
        store.delete()


def test_an_unknown_mode_is_refused_by_the_contract(client: TestClient) -> None:
    """Fails closed at the adapter, before any handler sees it."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("provider.select", {"mode": "whatever-is-fastest"}))
        reply = socket.receive_json()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "schema_violation"


def test_an_empty_key_is_refused_by_the_contract(client: TestClient) -> None:
    """An empty key would be stored, reported as has_key true, and then fail as an authentication
    error somewhere with nothing pointing back here."""
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        socket.send_text(envelope("credential.set", {"key": ""}))
        reply = socket.receive_json()

    assert reply["type"] == "error"
    assert reply["payload"]["code"] == "schema_violation"
    assert CredentialStore(target=TEST_TARGET).has_key() is False


def test_selecting_cloud_does_not_move_the_embedding_provider(client: TestClient) -> None:
    """docs/SECURITY.md section 11: embeddings are local in **both** modes.

    The half of that rule this test covers is the wiring. ``GeminiProvider.embed`` raising protects
    against the vault being exported; it does not protect against the index being handed the cloud
    provider and memory silently dying the moment the user flips the switch. What makes the rule
    structural is that ``create_app`` builds the index with the local provider and there is no code
    path in which the selected mode reaches that constructor argument.

    The behaviour behind it - that the local provider aims at loopback and only loopback while
    outbound is permitted - is asserted in test_memory_index.py::TestEmbeddingsNeverLeaveTheMachine.
    """
    with client, client.websocket_connect("/ws") as socket:
        handshake(socket)
        before = client.app.state.services.memory.index.provider

        socket.send_text(envelope("credential.set", {"key": KEY}))
        socket.receive_json()
        socket.send_text(envelope("provider.select", {"mode": "cloud"}))
        socket.receive_json()

        after = client.app.state.services.memory.index.provider

    assert client.app.state.services.egress.mode == "cloud"
    assert after is before
    assert after.name == "ollama"
