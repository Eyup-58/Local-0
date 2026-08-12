"""The vault index: incremental, and split by trust at the point of retrieval.

Two properties are worth more than the search quality here.

**The two search functions have two return types, and neither can produce the other's.** That is
invariant 2 of docs/SECURITY.md section 2, and it is why there is no ``search(trusted=...)`` - a flag
is a thing that gets defaulted wrong once, in a call somebody adds later, and the mistake is
invisible at the call site.

**Absent embeddings degrade loudly.** A model that is not installed must leave keyword search working
and say that it is keyword-only; search that quietly gets worse is the failure nobody reports because
nobody notices.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from local_zero_brain.audit import AuditLog
from local_zero_brain.credentials import Secret
from local_zero_brain.llm.gemini import GeminiProvider
from local_zero_brain.llm.ollama import OllamaProvider
from local_zero_brain.memory.chunks import TrustedChunk, UntrustedChunk
from local_zero_brain.memory.index import MemoryIndex
from local_zero_brain.net.egress import EgressGuard

TRUSTED_NOTE = """---
type: preference
source: user
---

# Interfaces

I prefer dark dense panels with hatched unavailable markers.
"""

AGENT_NOTE = """---
source: agent
---

# Recalled

The user asked about panels and hatching earlier.
"""


def build_vault(root: Path) -> None:
    (root / "Memory").mkdir(parents=True)
    (root / "LocalZero").mkdir(parents=True)
    (root / "Memory" / "preferences.md").write_text(TRUSTED_NOTE, encoding="utf-8")
    (root / "LocalZero" / "recalled.md").write_text(AGENT_NOTE, encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    build_vault(root)
    return root


@pytest.fixture
def index(tmp_path: Path) -> MemoryIndex:
    return MemoryIndex(tmp_path / "memory.sqlite")


class StubEmbedder:
    """Returns a deterministic vector per text, so ranking is testable without a model."""

    name = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise NotImplementedError

    def embed(self, texts) -> list[list[float]]:
        self.calls += 1
        return [[float(len(text)), float(text.count("panel")), 1.0] for text in texts]


class RefusingEmbedder(StubEmbedder):
    """A provider with no embedding model behind it - today's actual state of this machine."""

    def embed(self, texts) -> list[list[float]]:
        raise RuntimeError("no embedding model is installed")


class TestIndexing:
    def test_both_notes_are_indexed(self, index: MemoryIndex, vault: Path) -> None:
        report = index.reindex(vault)

        assert report.indexed == 2
        assert index.status().notes == 2

    def test_an_unchanged_note_is_not_reprocessed(self, index: MemoryIndex, vault: Path) -> None:
        """Incremental means incremental. Re-embedding an unchanged vault on every scan is the
        difference between a background task and a fan spinning up."""
        index.reindex(vault)

        report = index.reindex(vault)

        assert report.indexed == 0
        assert report.skipped == 2

    def test_an_edited_note_is_reprocessed(self, index: MemoryIndex, vault: Path) -> None:
        index.reindex(vault)
        note = vault / "Memory" / "preferences.md"
        note.write_text(TRUSTED_NOTE + "\n\n# Later\n\nI changed my mind about density.", encoding="utf-8")

        report = index.reindex(vault)

        assert report.indexed == 1
        assert index.search_trusted("density")

    def test_a_deleted_note_leaves_nothing_behind(self, index: MemoryIndex, vault: Path) -> None:
        """A memory that survives its note is a memory the user cannot correct by editing the vault,
        which is the entire reason the vault is the source of truth."""
        index.reindex(vault)
        (vault / "Memory" / "preferences.md").unlink()

        report = index.reindex(vault)

        assert report.removed == 1
        assert index.search_trusted("panels") == []

    def test_reindexing_an_absent_vault_is_not_an_error(self, index: MemoryIndex, tmp_path: Path) -> None:
        report = index.reindex(tmp_path / "no-vault-here")

        assert report.indexed == 0
        assert index.status().notes == 0


class TestTheTrustSplit:
    def test_trusted_search_returns_trusted_chunks(self, index: MemoryIndex, vault: Path) -> None:
        index.reindex(vault)

        results = index.search_trusted("panels")

        assert results
        assert all(isinstance(chunk, TrustedChunk) for chunk in results)

    def test_untrusted_search_returns_untrusted_chunks(self, index: MemoryIndex, vault: Path) -> None:
        index.reindex(vault)

        results = index.search_untrusted("panels")

        assert results
        assert all(isinstance(chunk, UntrustedChunk) for chunk in results)

    def test_the_agent_note_never_appears_in_trusted_results(self, index: MemoryIndex, vault: Path) -> None:
        """Both notes mention panels. Only one of them may reach the planner."""
        index.reindex(vault)

        paths = [chunk.note_path for chunk in index.search_trusted("panels")]

        assert all("LocalZero" not in path for path in paths)

    def test_the_user_note_never_appears_in_untrusted_results(self, index: MemoryIndex, vault: Path) -> None:
        index.reindex(vault)

        sources = [chunk.source for chunk in index.search_untrusted("panels")]

        assert all("Memory" not in source for source in sources)

    def test_there_is_no_search_function_taking_a_trust_flag(self, index: MemoryIndex) -> None:
        """Invariant 2 in the shape it actually gets broken: one function, one flag, one default."""
        assert not hasattr(index, "search")

    def test_moving_an_agent_note_into_a_trusted_folder_does_not_promote_it(
        self, index: MemoryIndex, vault: Path
    ) -> None:
        """Location grants trust; frontmatter can still refuse it.

        A note stamped `source: agent` stays untrusted wherever it is moved, so a user who tidies
        their vault by dragging folders around does not silently promote text the agent wrote.
        """
        index.reindex(vault)
        (vault / "Knowledge").mkdir()
        (vault / "LocalZero" / "recalled.md").rename(vault / "Knowledge" / "recalled.md")

        index.reindex(vault)

        assert not [chunk for chunk in index.search_trusted("panels") if "Knowledge" in chunk.note_path]
        assert [chunk for chunk in index.search_untrusted("panels") if "Knowledge" in chunk.source]

    def test_moving_an_unstamped_note_into_a_trusted_folder_does_promote_it(
        self, index: MemoryIndex, vault: Path
    ) -> None:
        """The other half: promotion is a human moving a file, and it genuinely works.

        Without this case the rule could be "nothing is ever trusted" and every test above would
        still pass.
        """
        (vault / "Inbox").mkdir()
        (vault / "Inbox" / "clipped.md").write_text("# Clipped\n\nsomething about panels", encoding="utf-8")
        index.reindex(vault)
        assert not [chunk for chunk in index.search_trusted("panels") if "Inbox" in chunk.note_path]

        (vault / "Inbox" / "clipped.md").rename(vault / "Memory" / "clipped.md")
        index.reindex(vault)

        assert [chunk for chunk in index.search_trusted("panels") if "clipped.md" in chunk.note_path]


class TestQueryHandling:
    def test_a_query_with_fts_syntax_in_it_does_not_blow_up(self, index: MemoryIndex, vault: Path) -> None:
        """A query can be derived from a model's own words, so it is untrusted input to sqlite.

        FTS5 has an expression grammar - quotes, NEAR, column filters, `*` - and feeding raw text
        into MATCH is both a crash and an injection surface.
        """
        index.reindex(vault)

        # It must not raise, and it must not match the way the injected operator intended. The
        # word characters survive as ordinary tokens, so "panels" still matches - what is gone is
        # the ability to write an expression.
        hostile = index.search_trusted('panels" OR chunks_fts MATCH "')
        assert isinstance(hostile, list)
        assert all(isinstance(chunk, TrustedChunk) for chunk in hostile)

        assert isinstance(index.search_trusted("NEAR("), list)
        assert isinstance(index.search_trusted("*"), list)

    def test_an_empty_query_returns_nothing_rather_than_everything(
        self, index: MemoryIndex, vault: Path
    ) -> None:
        index.reindex(vault)

        assert index.search_trusted("   ") == []

    def test_results_are_capped(self, index: MemoryIndex, vault: Path) -> None:
        index.reindex(vault)

        assert len(index.search_untrusted("panels", limit=1)) <= 1


class TestEmbeddings:
    def test_without_a_provider_the_index_says_it_is_keyword_only(
        self, index: MemoryIndex, vault: Path
    ) -> None:
        index.reindex(vault)

        assert index.status().embeddings_available is False

    def test_with_a_provider_vectors_are_stored(self, tmp_path: Path, vault: Path) -> None:
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=StubEmbedder())

        index.reindex(vault)

        assert index.status().embeddings_available is True
        assert index.status().embedded_chunks > 0

    def test_a_restarted_index_still_searches_by_meaning(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """A fresh process over an already-indexed vault must not silently fall back to keywords.

        The bug this covers: embeddings_available starts False and only a successful embedding sets
        it. The vector pass used to be gated on that flag *before* anything had tried, so a restart
        that reindexed nothing new skipped vector search entirely - with every chunk's embedding
        already in the database. Embedding the query is the probe, so it has to run first.
        """
        store = tmp_path / "memory.sqlite"
        MemoryIndex(store, provider=StubEmbedder()).reindex(vault)

        # A second index over the same file: nothing to reindex, so nothing embeds at startup.
        restarted = MemoryIndex(store, provider=StubEmbedder())
        assert restarted.status().embeddings_available is False, "precondition: nothing probed yet"

        restarted.search_trusted("anything", limit=3)

        assert restarted.status().embeddings_available is True

    def test_a_provider_with_no_embedding_model_degrades_to_keyword_search(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """Keyword search must keep working when no embedding model answers.

        Keyword search must keep working, and the status must say which mode is in force rather than
        quietly returning worse results.
        """
        logged: list[str] = []
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=RefusingEmbedder(), log=logged.append)

        index.reindex(vault)

        assert index.status().embeddings_available is False
        assert index.search_trusted("panels")
        assert any("embed" in line.lower() for line in logged)

    def test_a_note_is_recalled_by_meaning_when_no_word_matches(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """The point of having embeddings at all.

        Found by running it against the real model: with recall bounded by the keyword pass, asking
        for "GPU" returned nothing while a note said "graphics card" - which is keyword search with
        an extra step, not semantic search. The vector pass has to be able to find a chunk the
        keyword pass never nominated.
        """

        class Synonyms(StubEmbedder):
            """Maps two different words onto the same direction, which is what an embedding does."""

            def embed(self, texts) -> list[list[float]]:
                return [
                    [1.0, 0.0] if ("graphics" in text.lower() or "gpu" in text.lower()) else [0.0, 1.0]
                    for text in texts
                ]

        (vault / "Memory" / "card.md").write_text(
            "# Graphics card\n\nI use an RX 7800 XT.", encoding="utf-8"
        )
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=Synonyms())
        index.reindex(vault)

        assert [chunk for chunk in index.search_trusted("GPU") if "card.md" in chunk.note_path]

    def test_an_unrelated_question_recalls_nothing_even_with_embeddings_on(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """A vector scan always has a nearest neighbour, so without a floor everything is a hit.

        Measured against the real model before this was added: asking about submarines returned both
        notes in a two-note vault, and that text would have gone into the prompt. Recall that always
        returns something is recall that means nothing.
        """

        class Orthogonal(StubEmbedder):
            """Three directions: graphics, interface, and everything else.

            A two-direction stub would put the unrelated query on top of whichever note was not
            about graphics, and the test would fail for a reason that has nothing to do with the
            floor.
            """

            def embed(self, texts) -> list[list[float]]:
                vectors = []
                for text in texts:
                    lowered = text.lower()
                    if "graphics" in lowered:
                        vectors.append([1.0, 0.0, 0.0])
                    elif "panel" in lowered or "interface" in lowered:
                        vectors.append([0.0, 1.0, 0.0])
                    else:
                        vectors.append([0.0, 0.0, 1.0])

                return vectors

        (vault / "Memory" / "card.md").write_text("# Graphics card\n\nan RX 7800 XT", encoding="utf-8")
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=Orthogonal())
        index.reindex(vault)

        assert index.status().embeddings_available is True
        assert index.search_trusted("submarines") == []

    def test_keyword_only_recall_does_not_invent_matches(self, index: MemoryIndex, vault: Path) -> None:
        """The other side of it: without embeddings, a word that is not there finds nothing.

        Without this, the test above could pass because search had quietly become "return
        everything".
        """
        index.reindex(vault)

        assert index.search_trusted("submarine") == []

    def test_the_failure_is_reported_once_rather_than_per_chunk(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """A missing model is one fact. Logged per chunk it becomes a wall of text that hides
        whatever else went wrong in the same run."""
        logged: list[str] = []
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=RefusingEmbedder(), log=logged.append)

        index.reindex(vault)

        assert len([line for line in logged if "embed" in line.lower()]) == 1


class TestEmbeddingsNeverLeaveTheMachine:
    """docs/SECURITY.md section 11: embeddings are local in **both** modes.

    Indexing the vault means embedding its entire contents, so an embedding call that crossed the
    network would ship the user's own notes to a provider one chunk at a time - a bulk export
    arriving as a side effect of a switch the user flipped to get a better answer to one question.

    Cloud mode is the mode under test throughout, and deliberately so. Asserting this in Local mode
    would prove nothing: the egress guard blocks everything non-loopback there anyway, so a test
    that passed would be measuring the guard rather than the rule. In Cloud mode the door is open
    and a departure would be permitted and merely recorded - which makes "the vault's contents did
    not go anywhere" a statement about where the code aims rather than about what stopped it.
    """

    def test_indexing_a_vault_in_cloud_mode_reaches_loopback_and_nothing_else(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """The whole vault is indexed with outbound permitted, and every address touched is local.

        The underlying connect is replaced, so this needs no model server and makes no real
        request; what is captured is the destination the embedding path aimed at.

        ``_connect`` is restored **by hand, before ``uninstall``**, and not with ``monkeypatch``.
        The guard's saved original and the function its patch delegates to are the same attribute,
        so a teardown that runs after ``uninstall`` restores the stub onto ``socket.socket`` and
        leaves it there for every test that follows. Written out because it cost a full-suite
        failure that looked like the guard's bug rather than this test's.
        """
        attempted: list[tuple[str, int]] = []
        audit = AuditLog(tmp_path / "audit.jsonl")
        guard = EgressGuard(audit=audit, mode="cloud")

        def record_and_refuse(sock: socket.socket, address: object) -> None:
            attempted.append(address)  # type: ignore[arg-type]
            raise ConnectionRefusedError("no server in this test")

        original = guard._connect
        guard._connect = record_and_refuse  # type: ignore[assignment]
        guard.install()
        try:
            MemoryIndex(tmp_path / "memory.sqlite", provider=OllamaProvider()).reindex(vault)
        finally:
            guard._connect = original  # type: ignore[assignment]
            guard.uninstall()

        assert attempted, "the embedding path made no request at all, so this proves nothing"
        assert all(host == "127.0.0.1" for host, _ in attempted)
        # Cloud mode records every departure. No file means there was nothing to record.
        assert not audit.path.exists()

    def test_the_cloud_provider_cannot_embed_at_all(self) -> None:
        """The rule is code rather than convention.

        ``GeminiProvider.embed`` raising is what makes the test above hold for a caller that wires
        the selected chat provider into the index by mistake: the mistake fails loudly instead of
        exporting the vault.
        """
        with pytest.raises(NotImplementedError):
            GeminiProvider(key=Secret("local-zero-test-value-1111111111111111")).embed(["a note"])
