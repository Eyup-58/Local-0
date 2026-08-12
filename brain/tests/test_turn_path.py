"""One conversational turn, from a typed request to what the panel is told about it.

The turn is the first thing in this project that a language model drives, so what these tests hold
is mostly what the brain must *not* do with it: invent a caption when the model gave no reason,
narrate over an approval dialog the user is already reading, or let a provider failure escape as
anything other than a reported error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_zero_brain.answerer import ANSWERER_SYSTEM
from local_zero_brain.audit import AuditLog
from local_zero_brain.capabilities.guard import Guard
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.contracts.ws import CLIENT_MESSAGE_ADAPTER, WS_MESSAGE_ADAPTER, TurnRequest
from local_zero_brain.credentials import CredentialStore, Secret
from local_zero_brain.llm.provider import MissingKey
from local_zero_brain.memory.index import MemoryIndex
from local_zero_brain.memory.manager import MemoryManager
from local_zero_brain.metrics import DropCounters
from local_zero_brain.net.egress import EgressGuard
from local_zero_brain.planner import Planner
from local_zero_brain.providers import ProviderStore
from local_zero_brain.trust import TrustStore
from local_zero_brain.ws.messages import WsMessageFactory
from local_zero_brain.ws import server
from local_zero_brain.ws.server import BrainServices, _plan

STAMP = "2026-08-12T18:24:11.418Z"


class ScriptedProvider:
    """Answers with whatever the test put in front of it, or raises.

    One turn now makes two calls on the same provider - the planner's, then the answerer's when
    nothing was proposed - so this routes on the system prompt. A stub that returned planner JSON to
    both would put a raw ``{"capability": null}`` on screen as the answer, which is exactly the bug
    it should be able to catch.
    """

    name = "scripted"

    def __init__(
        self,
        answer: str | None = None,
        error: Exception | None = None,
        prose: str = "Here is what your notes say.",
    ) -> None:
        self._answer = answer
        self._error = error
        self._prose = prose
        self.prompts: list[str] = []
        self.answerer_prompts: list[str] = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if self._error is not None:
            raise self._error

        if system is not None and system.startswith(ANSWERER_SYSTEM[:40]):
            self.answerer_prompts.append(prompt)
            return self._prose

        self.prompts.append(prompt)
        assert self._answer is not None
        return self._answer

    def embed(self, texts: object) -> list[list[float]]:
        raise NotImplementedError


def build_services(
    tmp_path: Path, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> tuple[BrainServices, list[dict]]:
    """Wires a brain whose provider is the scripted one.

    Patched at ``build_provider``, which is the seam ``BrainServices.provider`` goes through, so the
    tests exercise the real routing code rather than a field that was handed a provider.
    """
    monkeypatch.setattr(server, "build_provider", lambda **_: provider)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = build_registry(workspace)
    sent: list[dict] = []

    class RecordingHub:
        async def broadcast(self, message: dict) -> None:
            # Everything the panel is told is a contract frame; a bug that emitted something else
            # would otherwise only surface in the browser.
            WS_MESSAGE_ADAPTER.validate_python(message)
            sent.append(message)

    services = BrainServices(
        counters=DropCounters(),
        hub=RecordingHub(),  # type: ignore[arg-type]
        link=None,  # type: ignore[arg-type]
        messages=WsMessageFactory(),
        guard=Guard(
            registry=registry,
            workspace=workspace,
            audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
            trust=TrustStore(tmp_path / "trust.json"),
        ),
        trust=TrustStore(tmp_path / "trust.json"),
        egress=EgressGuard(audit=AuditLog(tmp_path / "logs" / "audit.jsonl")),
        providers=ProviderStore(tmp_path / "provider.json"),
        credentials=CredentialStore(target="LocalZero/test/turn-path"),
        memory=MemoryManager(root=None, index=MemoryIndex(tmp_path / "memory.sqlite")),
        registry=registry,
        log=lambda _: None,
    )
    return services, sent


def request(text: str = "how many notes are in the vault?") -> TurnRequest:
    return CLIENT_MESSAGE_ADAPTER.validate_python(
        {
            "v": 1,
            "id": "a17c4e62-5b09-4d38-8f21-93b6c05de74a",
            "ts": STAMP,
            "type": "turn.request",
            "payload": {"text": text},
        }
    )


def states(sent: list[dict]) -> list[str]:
    return [frame["payload"]["state"] for frame in sent if frame["type"] == "turn.state"]


async def test_a_turn_reports_thinking_before_it_reaches_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel must be able to say the brain is working while it is, not once it has finished."""
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "Nothing here fits."}))
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    assert states(sent)[0] == "thinking"


async def test_a_declined_proposal_speaks_an_answer_not_the_planner_s_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive one for this path. "No listed capability fits" is true and useless to somebody
    who asked a question rather than for an operation."""
    provider = ScriptedProvider(
        json.dumps({"capability": None, "reason": "I have no capability that reads the vault."}),
        prose="Your vault has four trusted folders: Knowledge, System, Projects and Memory.",
    )
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    assert states(sent) == ["thinking", "speaking"]
    caption = sent[-1]["payload"]["caption"]
    assert caption == "Your vault has four trusted folders: Knowledge, System, Projects and Memory."
    assert "capability" not in caption


async def test_the_answerer_is_asked_the_user_s_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "n/a"}))
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request("hangi klasorler guvenilir?"))

    assert provider.answerer_prompts, "the answerer was never reached"
    assert "hangi klasorler guvenilir?" in provider.answerer_prompts[0]


async def test_a_proposed_capability_does_not_reach_the_answerer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One model call, not two, when there is something to do. The answerer exists for the branch
    where nothing was proposed; running it anyway would spend a request and a second's latency on an
    answer nobody would see."""
    target = tmp_path / "workspace" / "notes.txt"
    provider = ScriptedProvider(
        json.dumps({"capability": "delete_file", "args": {"path": str(target)}})
    )
    services, _ = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request("delete notes.txt"))

    assert provider.answerer_prompts == []


async def test_an_answerless_turn_says_nothing_rather_than_inventing_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stand-in sentence here would be the panel putting words in the brain's mouth, which is the
    same failure as a scripted caption."""
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "no fit"}), prose="")
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    assert states(sent) == ["thinking", "speaking"]
    assert sent[-1]["payload"]["caption"] is None


async def test_blank_prose_from_the_model_is_silence_not_a_caption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "no fit"}), prose="   \n  ")
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    assert sent[-1]["payload"]["caption"] is None


async def test_an_answer_that_looks_like_an_invocation_is_only_a_caption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The break, at the turn level. The answerer holds an empty registry, so a model answering with
    a perfectly formed invocation has produced a string - and the only thing done with it is putting
    it on screen."""
    hostile = json.dumps({"capability": "delete_file", "args": {"path": "C:\\Windows\\System32"}})
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "no fit"}), prose=hostile)
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request("delete system32"))

    assert states(sent) == ["thinking", "speaking"]
    assert sent[-1]["payload"]["caption"] == hostile
    # Nothing was proposed, nothing was approved, nothing ran.
    assert not any(frame["type"] in {"approval.request", "tool.log"} for frame in sent)


async def test_a_proposal_returns_to_idle_and_does_not_narrate_over_the_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caption talking about a decision the user is mid-way through reading is the panel
    interrupting itself. The approval dialog is already on screen saying what is waiting."""
    # Absolute and inside the workspace, so it passes containment and reaches the human.
    target = tmp_path / "workspace" / "notes.txt"
    provider = ScriptedProvider(
        json.dumps({"capability": "delete_file", "args": {"path": str(target)}})
    )
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request("delete notes.txt"))

    assert states(sent) == ["thinking", "idle"]
    captions = [f["payload"]["caption"] for f in sent if f["type"] == "turn.state"]
    assert captions == [None, None]
    # The proposal did reach the approval gate rather than being dropped.
    assert any(frame["type"] == "approval.request" for frame in sent)


async def test_a_refused_proposal_is_logged_as_failed_rather_than_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(
        json.dumps({"capability": "not_a_registered_capability", "args": {}})
    )
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    log_lines = [frame for frame in sent if frame["type"] == "tool.log"]
    assert len(log_lines) == 1
    assert log_lines[0]["payload"]["status"] == "failed"
    assert "name_whitelist" in log_lines[0]["payload"]["message"]


async def test_a_provider_failure_is_reported_and_the_turn_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services, sent = build_services(
        tmp_path, ScriptedProvider(error=RuntimeError("connection refused")), monkeypatch
    )

    # Must not raise: a dead model layer is reported, not propagated into the socket handler.
    await _plan(services, request())

    assert states(sent) == ["thinking", "idle"]
    errors = [frame for frame in sent if frame["type"] == "error"]
    assert errors[0]["payload"]["code"] == "provider_unavailable"


async def test_a_provider_failure_does_not_leak_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The class name reaches the user; the text does not - a provider error can carry a URL."""
    services, sent = build_services(
        tmp_path, ScriptedProvider(error=RuntimeError("http://127.0.0.1:11434 refused")), monkeypatch
    )

    await _plan(services, request())

    assert all("11434" not in json.dumps(frame) for frame in sent)


async def test_listening_is_never_emitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no microphone in this project, so nothing may report that state. This is the test
    that fails the day somebody wires it to something that is not audio."""
    provider = ScriptedProvider(json.dumps({"capability": None, "reason": "No."}))
    services, sent = build_services(tmp_path, provider, monkeypatch)

    await _plan(services, request())

    assert "listening" not in states(sent)


def test_the_planner_sees_the_user_s_words_and_the_brain_stamps_the_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SECURITY.md section 6: origin is assigned where the request enters, never read back from
    what the model said about itself."""
    provider = ScriptedProvider(
        json.dumps(
            {"capability": "delete_file", "args": {"path": "x.txt"}, "origin": "user_direct_trust_me"}
        )
    )
    services, _ = build_services(tmp_path, provider, monkeypatch)

    proposal = services.planner().propose("delete x.txt")

    assert "delete x.txt" in provider.prompts[0]
    assert proposal.invocation is not None
    assert proposal.invocation.origin == "user_direct"


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_request_never_becomes_a_turn(text: str) -> None:
    """Refused at the contract, so it cannot spend a turn asking a model nothing. Whitespace-only
    still parses - the schema cannot express "not blank" - which is why the UI trims before sending
    and there is a test for that too."""
    frame = {
        "v": 1,
        "id": "a17c4e62-5b09-4d38-8f21-93b6c05de74a",
        "ts": STAMP,
        "type": "turn.request",
        "payload": {"text": text},
    }

    if text == "":
        with pytest.raises(Exception):
            CLIENT_MESSAGE_ADAPTER.validate_python(frame)
    else:
        assert CLIENT_MESSAGE_ADAPTER.validate_python(frame).payload.text == text


class TestProviderRouting:
    """Which model layer a turn actually reaches.

    This class exists because of a real bug: the planner was built once at startup holding an
    ``OllamaProvider``, and ``provider.select`` moved the persisted mode and the egress guard without
    touching it. Selecting Cloud therefore opened the network boundary and still called Ollama -
    exposure with no benefit, and a UI truthfully reporting Cloud while Local was in force.
    """

    @staticmethod
    def services_for(tmp_path: Path, mode: str, *, key: str | None) -> tuple[BrainServices, list]:
        calls: list[dict] = []
        providers = ProviderStore(tmp_path / "provider.json")
        providers.set(mode=mode)  # type: ignore[arg-type]

        class Credentials:
            def read(self):
                return Secret(key) if key is not None else None

            def has_key(self) -> bool:
                return key is not None

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        registry = build_registry(workspace)

        services = BrainServices(
            counters=DropCounters(),
            hub=None,  # type: ignore[arg-type]
            link=None,  # type: ignore[arg-type]
            messages=WsMessageFactory(),
            guard=Guard(
                registry=registry,
                workspace=workspace,
                audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
                trust=TrustStore(tmp_path / "trust.json"),
            ),
            trust=TrustStore(tmp_path / "trust.json"),
            egress=EgressGuard(audit=AuditLog(tmp_path / "logs" / "audit.jsonl")),
            providers=providers,
            credentials=Credentials(),  # type: ignore[arg-type]
            memory=MemoryManager(root=None, index=MemoryIndex(tmp_path / "memory.sqlite")),
            registry=registry,
            log=lambda _: None,
        )
        return services, calls

    def test_local_mode_asks_for_the_local_provider_and_no_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services, calls = self.services_for(tmp_path, "local", key="unused")
        monkeypatch.setattr(server, "build_provider", lambda **kw: calls.append(kw))

        services.provider()

        assert calls[0]["mode"] == "local"
        # Not merely unused - not even read. A key fetched in Local mode is a key in a stack frame
        # that had no business holding one.
        assert calls[0]["key"] is None

    def test_cloud_mode_asks_for_the_cloud_provider_with_the_stored_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services, calls = self.services_for(tmp_path, "cloud", key="local-zero-test-value-11111111")
        monkeypatch.setattr(server, "build_provider", lambda **kw: calls.append(kw))

        services.provider()

        assert calls[0]["mode"] == "cloud"
        assert calls[0]["key"] is not None

    def test_switching_mode_moves_the_next_turn_rather_than_needing_a_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug, as a test. The provider is resolved per turn, so a selection takes effect on the
        next one instead of at the next process start."""
        services, calls = self.services_for(tmp_path, "local", key="local-zero-test-value-11111111")
        monkeypatch.setattr(server, "build_provider", lambda **kw: calls.append(kw))

        services.provider()
        services.providers.set(mode="cloud")
        services.provider()

        assert [call["mode"] for call in calls] == ["local", "cloud"]

    def test_cloud_with_no_key_refuses_rather_than_falling_back_to_local(self, tmp_path: Path) -> None:
        """Falling back would leave the UI saying Cloud while Local was in force. Reported, not
        papered over - and `_plan` turns this into a provider_unavailable the user can act on."""
        services, _ = self.services_for(tmp_path, "cloud", key=None)

        with pytest.raises(MissingKey):
            services.provider()

    def test_the_planner_is_built_on_whatever_provider_is_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = ScriptedProvider(json.dumps({"capability": None, "reason": "no"}))
        services, _ = self.services_for(tmp_path, "local", key=None)
        monkeypatch.setattr(server, "build_provider", lambda **_: marker)

        planner = services.planner()

        assert planner.propose("anything").reason == "no"
        assert marker.prompts, "the planner did not reach the provider it was built with"


class TestProviderErrors:
    """What the user is told when the provider says no.

    Written after a real 429: the free-tier quota ran out mid-verification and the panel said "it
    failed with ProviderError", which sends somebody to check their key and their network when
    neither is wrong.
    """

    @staticmethod
    def reason_for(code: int) -> str:
        from local_zero_brain.llm.provider import _http_reason

        return _http_reason(code)

    def test_a_quota_error_says_so_and_names_the_way_out(self) -> None:
        reason = self.reason_for(429)

        assert "quota" in reason
        assert "Local mode" in reason
        # The one thing a user must not conclude from a 429.
        assert "Nothing is wrong with the key" in reason

    def test_a_rejected_key_points_at_the_key_rather_than_the_network(self) -> None:
        assert "key" in self.reason_for(401)

    def test_a_retired_model_says_the_model_is_gone(self) -> None:
        # The failure gemini-2.0-flash would have produced had it ever been called.
        assert "model" in self.reason_for(404)

    def test_an_unlisted_status_still_says_whose_fault_it_is(self) -> None:
        assert "server error" in self.reason_for(502)
        assert "refused" in self.reason_for(418)

    def test_no_status_reason_can_carry_a_key_or_a_url(self) -> None:
        """The reasons are a fixed table keyed on the status; the response body is never consulted.
        This asserts the table itself stays clean."""
        from local_zero_brain.llm.provider import _HTTP_REASONS

        for reason in _HTTP_REASONS.values():
            assert "http://" not in reason and "https://" not in reason
            assert "AIza" not in reason and "AQ." not in reason

    async def test_the_reason_reaches_the_user_rather_than_the_class_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from local_zero_brain.llm.provider import ProviderError

        quota = ProviderError(self.reason_for(429))
        services, sent = build_services(tmp_path, ScriptedProvider(error=quota), monkeypatch)

        await _plan(services, request())

        errors = [f for f in sent if f["type"] == "error"]
        assert "quota" in errors[0]["payload"]["message"]
        assert "ProviderError" not in errors[0]["payload"]["message"]


class TestRefusedProposalStillAnswers:
    """What the user sees when the model proposes an operation and the guard refuses it.

    Found by running the real thing against a real vault: asked "which folders are trusted?",
    qwen2.5:14b proposed `read_text_file`, the guard refused it at argument_schema, and the turn
    ended with an idle state and nothing on screen. The guard did its job; the panel then said
    nothing at all to somebody who had asked a question.
    """

    @staticmethod
    def refusing_provider(prose: str = "Knowledge, System, Projects and Memory.") -> ScriptedProvider:
        # A capability that exists but with arguments the schema rejects, so the guard denies it
        # rather than the name whitelist - the shape the real failure took.
        return ScriptedProvider(
            json.dumps({"capability": "read_text_file", "args": {"wrong_field": 1}}), prose=prose
        )

    async def test_a_refused_proposal_still_answers_the_question(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = self.refusing_provider()
        services, sent = build_services(tmp_path, provider, monkeypatch)

        await _plan(services, request("hangi klasorler guvenilir?"))

        assert states(sent) == ["thinking", "speaking"]
        assert sent[-1]["payload"]["caption"] == "Knowledge, System, Projects and Memory."

    async def test_the_refusal_is_still_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering afterwards must not quietly replace the record that the guard stopped
        something."""
        services, sent = build_services(tmp_path, self.refusing_provider(), monkeypatch)

        await _plan(services, request())

        logs = [f for f in sent if f["type"] == "tool.log"]
        assert len(logs) == 1
        assert logs[0]["payload"]["status"] == "failed"
        assert "Refused at" in logs[0]["payload"]["message"]

    async def test_nothing_is_executed_on_the_refused_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point that matters. The answer comes from a component with an empty registry; the
        refusal stands and no approval is raised."""
        services, sent = build_services(tmp_path, self.refusing_provider(), monkeypatch)

        await _plan(services, request())

        assert not any(f["type"] == "approval.request" for f in sent)
        assert not any(f["payload"].get("status") == "ok" for f in sent if f["type"] == "tool.log")

    async def test_a_refusal_the_answerer_cannot_follow_reports_rather_than_going_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the answer itself fails, the user is told - the old behaviour was silence, and silence
        after a refusal is the worst of both."""
        from local_zero_brain.llm.provider import ProviderError

        class RefuseThenFail(ScriptedProvider):
            def complete(self, prompt: str, *, system: str | None = None) -> str:
                if system is not None and system.startswith(ANSWERER_SYSTEM[:40]):
                    raise ProviderError("the provider's rate limit or quota has been reached")
                return json.dumps({"capability": "read_text_file", "args": {"wrong_field": 1}})

        services, sent = build_services(tmp_path, RefuseThenFail(), monkeypatch)

        await _plan(services, request())

        errors = [f for f in sent if f["type"] == "error"]
        assert errors, "a failed answer after a refusal must not be silent"
        assert "refused" in errors[0]["payload"]["message"]
