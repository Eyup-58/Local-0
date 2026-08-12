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

from pathlib import Path

import pytest

from local_zero_brain.memory.chunks import TrustedChunk, UntrustedChunk
from local_zero_brain.memory.index import MemoryIndex

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

    def test_a_provider_with_no_embedding_model_degrades_to_keyword_search(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """Today's actual state of this machine: gemma4 is installed, nomic-embed-text is not.

        Keyword search must keep working, and the status must say which mode is in force rather than
        quietly returning worse results.
        """
        logged: list[str] = []
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=RefusingEmbedder(), log=logged.append)

        index.reindex(vault)

        assert index.status().embeddings_available is False
        assert index.search_trusted("panels")
        assert any("embed" in line.lower() for line in logged)

    def test_the_failure_is_reported_once_rather_than_per_chunk(
        self, tmp_path: Path, vault: Path
    ) -> None:
        """A missing model is one fact. Logged per chunk it becomes a wall of text that hides
        whatever else went wrong in the same run."""
        logged: list[str] = []
        index = MemoryIndex(tmp_path / "memory.sqlite", provider=RefusingEmbedder(), log=logged.append)

        index.reindex(vault)

        assert len([line for line in logged if "embed" in line.lower()]) == 1
