"""Memory that understands time, and conflicts that are surfaced rather than settled.

Two rules, and the second is the one that keeps this honest:

**A memory that is no longer current stops being recalled, and is not destroyed.** `status:
superseded` and `status: archived` drop out of recall; the note stays in the vault, where the user
can read it, correct it, or move it back. Deleting history to keep an answer tidy is the behaviour
the user's own specification refused ("never silently destroy important memory").

**Conflicts are reported, never resolved.** Deciding which of two contradictory notes is true is a
judgement, and a judgement made silently by a keyword heuristic is worse than no judgement at all.
What the code does is notice that two active notes of the same kind are talking about the same thing
and say so.

A typo in `status:` reads as active. Hiding a note the user wrote because they misspelled a
frontmatter value would lose their memory to a spelling mistake, and the vault is meant to be the
half of this system a human can trust.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_zero_brain.memory.index import MemoryIndex
from local_zero_brain.memory.vault import parse_note

CURRENT = """---
type: fact
source: user
status: active
---

# Graphics card

I use an RX 7800 XT with 16 GB of video memory.
"""

OLD = """---
type: fact
source: user
status: superseded
---

# Graphics card

I use an RX 7600.
"""

ARCHIVED = """---
type: decision
source: user
status: archived
---

# Browser

Universal search opens Chrome.
"""

SUPERSEDING = """---
type: fact
source: user
supersedes: Memory/old-card.md
---

# Graphics card, again

I upgraded to an RX 7800 XT.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Memory").mkdir(parents=True)
    return root


@pytest.fixture
def index(tmp_path: Path) -> MemoryIndex:
    return MemoryIndex(tmp_path / "memory.sqlite")


def write(vault: Path, name: str, text: str) -> Path:
    path = vault / "Memory" / name
    path.write_text(text, encoding="utf-8")
    return path


class TestStatusIsRead:
    def test_status_and_supersedes_are_parsed(self) -> None:
        metadata, _ = parse_note(SUPERSEDING)

        assert metadata.status is None
        assert metadata.supersedes == "Memory/old-card.md"

    def test_a_note_with_no_status_is_active(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "card.md", "# Card\n\nI use an RX 7800 XT.")
        index.reindex(vault)

        assert index.search_trusted("7800")


class TestRecallFollowsStatus:
    def test_an_active_note_is_recalled(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "card.md", CURRENT)
        index.reindex(vault)

        assert index.search_trusted("7800")

    def test_a_superseded_note_is_not_recalled(self, index: MemoryIndex, vault: Path) -> None:
        """The point of the whole phase: asking about the present gets the present."""
        write(vault, "old-card.md", OLD)
        index.reindex(vault)

        assert index.search_trusted("7600") == []

    def test_an_archived_note_is_not_recalled(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "browser.md", ARCHIVED)
        index.reindex(vault)

        assert index.search_trusted("Chrome") == []

    def test_a_superseded_note_is_still_in_the_vault(self, index: MemoryIndex, vault: Path) -> None:
        """Not recalled is not deleted. The user can still read and correct it."""
        note = write(vault, "old-card.md", OLD)
        index.reindex(vault)

        assert note.exists()
        assert "RX 7600" in note.read_text(encoding="utf-8")

    def test_an_unrecognised_status_reads_as_active(self, index: MemoryIndex, vault: Path) -> None:
        """A typo must not silently remove a memory the user wrote."""
        write(vault, "card.md", CURRENT.replace("status: active", "status: activ"))
        index.reindex(vault)

        assert index.search_trusted("7800")


class TestSuperseding:
    def test_a_note_can_retire_another_by_naming_it(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "old-card.md", "---\ntype: fact\n---\n\n# Card\n\nI use an RX 7600.")
        write(vault, "new-card.md", SUPERSEDING)

        index.reindex(vault)

        assert index.search_trusted("7600") == []
        assert index.search_trusted("7800")

    def test_the_retired_note_is_not_deleted(self, index: MemoryIndex, vault: Path) -> None:
        old = write(vault, "old-card.md", "---\ntype: fact\n---\n\n# Card\n\nI use an RX 7600.")
        write(vault, "new-card.md", SUPERSEDING)

        index.reindex(vault)

        assert old.exists()

    def test_naming_a_note_that_does_not_exist_is_harmless(self, index: MemoryIndex, vault: Path) -> None:
        """Vaults get reorganised. A dangling reference is not a reason to fail a scan."""
        write(vault, "new-card.md", "---\ntype: fact\nsupersedes: Memory/never-existed.md\n---\n\n# Card\n\nan RX 7800 XT")

        report = index.reindex(vault)

        assert report.indexed == 1
        assert index.search_trusted("7800")

    def test_an_agent_note_cannot_retire_a_users_note(self, index: MemoryIndex, vault: Path) -> None:
        """The escalation this phase could have introduced.

        Superseding removes a memory from recall. If an agent-written note could do it, a model
        could quietly retire whatever the user wrote that stood in its way - achieving by deletion
        what it is not allowed to achieve by instruction.
        """
        (vault / "LocalZero").mkdir()
        write(vault, "card.md", CURRENT)
        (vault / "LocalZero" / "note.md").write_text(
            "---\nsource: agent\nsupersedes: Memory/card.md\n---\n\n# Note\n\nthat is out of date",
            encoding="utf-8",
        )

        index.reindex(vault)

        assert index.search_trusted("7800")


class TestConflicts:
    def test_two_active_notes_about_the_same_thing_are_surfaced(
        self, index: MemoryIndex, vault: Path
    ) -> None:
        write(vault, "card-a.md", CURRENT)
        write(vault, "card-b.md", "---\ntype: fact\nsource: user\n---\n\n# Graphics card\n\nI use an RX 7600 now.")
        index.reindex(vault)

        conflicts = index.conflict_candidates("Memory/card-b.md")

        assert "Memory/card-a.md" in conflicts

    def test_a_note_does_not_conflict_with_itself(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "card-a.md", CURRENT)
        index.reindex(vault)

        assert index.conflict_candidates("Memory/card-a.md") == []

    def test_a_superseded_note_is_not_a_conflict(self, index: MemoryIndex, vault: Path) -> None:
        """It has already been answered. Reporting it would make every correction look like a
        contradiction."""
        write(vault, "old-card.md", OLD)
        write(vault, "card-b.md", CURRENT)
        index.reindex(vault)

        assert index.conflict_candidates("Memory/card-b.md") == []

    def test_notes_of_different_kinds_are_not_conflicts(self, index: MemoryIndex, vault: Path) -> None:
        write(vault, "card.md", CURRENT)
        write(vault, "pref.md", "---\ntype: preference\nsource: user\n---\n\n# Graphics card\n\nI like the RX 7800 XT's fan curve.")
        index.reindex(vault)

        assert index.conflict_candidates("Memory/pref.md") == []

    def test_nothing_is_resolved_automatically(self, index: MemoryIndex, vault: Path) -> None:
        """Both notes stay recallable. Picking a winner is a judgement, and a keyword heuristic
        making it silently is worse than making none."""
        write(vault, "card-a.md", CURRENT)
        write(vault, "card-b.md", "---\ntype: fact\nsource: user\n---\n\n# Graphics card\n\nI use an RX 7600 now.")
        index.reindex(vault)

        index.conflict_candidates("Memory/card-b.md")

        assert len(index.search_trusted("graphics", limit=10)) >= 2
