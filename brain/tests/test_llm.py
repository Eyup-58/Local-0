"""The provider layer: one interface, two modes, and the failures that must not be silent.

docs/ROADMAP.md M4 asks for four things this file holds:

* providers work through a single interface, and keys never come from source;
* a missing key is a startup failure with a clear message, not a runtime surprise three screens in;
* malformed structured output is handled with a **bounded** retry - no infinite loop;
* no key appears in a log, an error message or a fixture.

The Ollama tests run against a real HTTP server on loopback rather than a mocked transport. The
thing worth proving is that a request this code builds is understood and that a response is parsed,
and a mock proves only that the code calls itself the way it calls itself. Loopback is also exactly
what the egress guard permits in Local mode, so the test exercises the real arrangement.

Gemini is never contacted. Its tests are about what happens *before* a request exists.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from local_zero_brain.audit import AuditLog
from local_zero_brain.credentials import Secret
from local_zero_brain.llm.gemini import GeminiProvider
from local_zero_brain.llm.ollama import LOCAL_TIMEOUT_SECONDS, OllamaProvider
from local_zero_brain.llm.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_STRUCTURED_ATTEMPTS,
    MalformedOutput,
    MissingKey,
    ProviderError,
    build_provider,
    complete_json,
    post_json,
)
from local_zero_brain.net.egress import EgressGuard

#: Not shaped like a vendor key on purpose - see the note in test_credentials.py.
KEY = "local-zero-test-value-0000000000000000"


class _Handler(BaseHTTPRequestHandler):
    """Answers whatever the enclosing test told it to, and remembers what it was asked."""

    responses: dict[str, dict] = {}
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append({"path": self.path, "body": body})

        payload = json.dumps(type(self).responses.get(self.path, {})).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        """Silence. The suite's output is not a place for a request log."""


@pytest.fixture
def ollama() -> Iterator[OllamaProvider]:
    _Handler.responses = {}
    _Handler.received = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    try:
        yield OllamaProvider(base_url=f"http://{host}:{port}", model="test-model")
    finally:
        server.shutdown()
        server.server_close()


def test_a_completion_returns_the_models_text(ollama: OllamaProvider) -> None:
    _Handler.responses["/api/chat"] = {"message": {"content": "the answer"}}

    assert ollama.complete("the question") == "the answer"


def test_the_system_prompt_is_sent_separately_from_the_user_text(ollama: OllamaProvider) -> None:
    """Not concatenated into one string.

    Concatenation is how a boundary between two kinds of text stops existing, and this is the
    boundary the whole threat model rests on.
    """
    _Handler.responses["/api/chat"] = {"message": {"content": "ok"}}

    ollama.complete("user text", system="system text")

    messages = _Handler.received[0]["body"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == "system text"
    assert messages[1]["content"] == "user text"


def test_embeddings_come_back_as_vectors(ollama: OllamaProvider) -> None:
    _Handler.responses["/api/embed"] = {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}

    assert ollama.embed(["one", "two"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_the_local_provider_waits_longer_than_the_shared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Because the two paths fail differently, and one measurement says so.

    A remote provider silent for a minute is not going to answer. A local one may just be slow:
    measured 2026-08-12, a reader answer over one small chunk took 55.6s on this machine, because
    the only installed model is 17 GB against 16 GB of VRAM and spills to CPU. At the shared 60s
    default that request timed out - and reported as "the provider could not be reached", which
    sends the user to check a network that is not involved.
    """
    import local_zero_brain.llm.ollama as ollama_module

    seen: dict[str, object] = {}

    def capture(url: str, payload: dict, *, headers=None, timeout: int = 0) -> dict:
        seen["timeout"] = timeout
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(ollama_module, "post_json", capture)
    OllamaProvider().complete("anything")

    assert seen["timeout"] == LOCAL_TIMEOUT_SECONDS
    assert LOCAL_TIMEOUT_SECONDS > DEFAULT_TIMEOUT_SECONDS


def test_a_response_missing_the_field_is_an_error_rather_than_an_empty_string(
    ollama: OllamaProvider,
) -> None:
    """Validate at the boundary. An empty answer and a malformed response are different facts."""
    _Handler.responses["/api/chat"] = {"unexpected": True}

    with pytest.raises(MalformedOutput):
        ollama.complete("the question")


class TestStructuredOutput:
    """The bounded retry. A loop that cannot terminate is worse than a failure that can."""

    class _Stubborn:
        """Never returns valid JSON, and counts how many times it was asked."""

        name = "stubborn"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str, *, system: str | None = None) -> str:
            self.calls += 1
            return "I would rather explain than answer."

        def embed(self, texts: object) -> list[list[float]]:
            raise NotImplementedError

    class _SecondTime:
        """Fails once, then complies - the case the retry exists for."""

        name = "second-time"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str, *, system: str | None = None) -> str:
            self.calls += 1
            return "not json" if self.calls == 1 else '{"ok": true}'

        def embed(self, texts: object) -> list[list[float]]:
            raise NotImplementedError

    def test_malformed_output_stops_after_a_fixed_number_of_attempts(self) -> None:
        provider = self._Stubborn()

        with pytest.raises(MalformedOutput):
            complete_json(provider, "give me json")

        assert provider.calls == MAX_STRUCTURED_ATTEMPTS

    def test_a_retry_that_succeeds_returns_the_parsed_object(self) -> None:
        provider = self._SecondTime()

        assert complete_json(provider, "give me json") == {"ok": True}

    def test_a_fenced_code_block_is_still_json(self) -> None:
        """Models wrap JSON in ``` fences constantly, and a retry over that is a wasted round trip."""

        class Fencing:
            name = "fencing"

            def complete(self, prompt: str, *, system: str | None = None) -> str:
                return '```json\n{"ok": true}\n```'

            def embed(self, texts: object) -> list[list[float]]:
                raise NotImplementedError

        assert complete_json(Fencing(), "give me json") == {"ok": True}


class TestCloudMode:
    """Everything here happens before a packet exists."""

    def test_cloud_mode_without_a_key_fails_at_construction(self) -> None:
        """A startup failure with a readable message, not a surprise on the first question."""
        with pytest.raises(MissingKey) as failure:
            build_provider(mode="cloud", key=None)

        assert "Credential Manager" in str(failure.value)

    def test_local_mode_needs_no_key(self) -> None:
        provider = build_provider(mode="local", key=None)

        assert provider.name == "ollama"

    def test_the_key_never_appears_in_the_providers_repr(self) -> None:
        provider = GeminiProvider(key=Secret(KEY))

        assert KEY not in repr(provider)
        assert KEY not in str(vars(provider) if hasattr(provider, "__dict__") else provider)

    def test_gemini_refuses_to_embed(self) -> None:
        """Embeddings are computed locally in every mode.

        Indexing the vault means embedding its entire contents, so an embedding call that crossed
        the network would ship the user's notes to a provider one chunk at a time - the exact thing
        Cloud mode is not supposed to imply. The refusal is here rather than in a comment.
        """
        provider = GeminiProvider(key=Secret(KEY))

        with pytest.raises(NotImplementedError):
            provider.embed(["anything"])

    def test_the_endpoint_is_pinned_rather_than_assembled_from_input(self) -> None:
        """SECURITY.md section 11: one pinned base URL is what the provider client contributes."""
        assert GeminiProvider(key=Secret(KEY)).base_url.startswith("https://")

    def test_a_call_blocked_by_local_mode_says_so_rather_than_reporting_a_dead_provider(self) -> None:
        """Found by running it, not by reading it.

        ``urllib`` wraps any OSError from the socket layer in a ``URLError``, and EgressBlocked is an
        OSError. Without unwrapping, a user who left the boundary in Local mode and selected a cloud
        model would be told the provider could not be reached - sending them to check their network,
        their key and the provider's status page, when the answer is a switch inside this product.
        """
        audit = AuditLog(Path(tempfile.mkdtemp()) / "audit.jsonl")
        guard = EgressGuard(audit=audit)
        guard.install()

        try:
            with pytest.raises(ProviderError) as failure:
                post_json("https://203.0.113.7/v1/anything", {})
        finally:
            guard.uninstall()

        assert "loopback" in str(failure.value).lower()
