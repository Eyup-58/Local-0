"""The one door to memory. Everything else in the brain goes through this.

Not an abstraction for its own sake: the vault and the index are two things that have to agree, and
scattering "read the vault" and "query the index" across the planner, the reader and the socket
handler is how they stop agreeing. One object owns both.

**Two recall methods rather than one returning both.** A single ``recall()`` handing back an object
with a ``trusted`` field and an ``untrusted`` field is exactly what docs/SECURITY.md section 2
invariant 2 forbids, wearing a struct: the two kinds would travel together, and the first caller to
pass the whole result somewhere convenient would carry untrusted text into the planner.

**Memory is optional.** No vault configured, a renamed folder, an unplugged drive: each leaves this
object answering "nothing" and the rest of the product working. Telemetry, approval and the guard do
not depend on memory, and a missing vault must not be able to take them down.

``remember()`` and ``forget()` are deliberately absent. Writing into the vault goes through
registered capabilities and the guard - a manager that could write notes directly would be a way
around the approval gate, and the whole point of putting agent notes in their own folder is that the
guard is what keeps them there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_zero_brain.llm.provider import Provider
from local_zero_brain.memory.chunks import TrustedChunk, UntrustedChunk
from local_zero_brain.memory.index import DEFAULT_LIMIT, MemoryIndex, ReindexReport
from local_zero_brain.memory.vault import vault_root

#: The most recalled text that may enter one prompt. A budget exists because "inject the whole
#: vault" is the failure mode a memory system arrives at by default, and it is expensive in exactly
#: the case where it is least useful - a large vault.
MAX_CONTEXT_CHARS = 4000


def _noop(_: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    """What the UI will show in phase 7, and what a human debugging this needs first."""

    enabled: bool
    vault: str | None
    notes: int
    chunks: int
    embedded_chunks: int
    last_indexed_at: str | None
    embeddings_available: bool


class MemoryManager:
    """Recall over the vault, split by trust and bounded by a budget."""

    __slots__ = ("_root", "_index", "_log")

    def __init__(self, *, root: Path | None, index: MemoryIndex, log: Callable[[str], None] = _noop) -> None:
        self._root = root
        self._index = index
        self._log = log

    @classmethod
    def from_environment(
        cls,
        *,
        index_path: Path | None = None,
        provider: Provider | None = None,
        log: Callable[[str], None] = _noop,
    ) -> MemoryManager:
        """Builds a manager from ``OBSIDIAN_VAULT_PATH``, disabled when there is no vault."""
        root = vault_root()
        if root is None:
            log("no vault is configured; memory is off and the rest of the brain is unaffected")

        return cls(
            root=root,
            index=MemoryIndex(index_path or MemoryIndex.default_path(), provider=provider, log=log),
            log=log,
        )

    @property
    def enabled(self) -> bool:
        return self._root is not None

    @property
    def index(self) -> MemoryIndex:
        return self._index

    def recall_trusted(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[TrustedChunk]:
        """Notes the user wrote. The only memory the planner may see."""
        if self._root is None:
            return []

        return _within_budget(self._index.search_trusted(query, limit=limit))

    def recall_untrusted(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[UntrustedChunk]:
        """Everything else, including notes Local Zero wrote itself. For the reader and the user."""
        if self._root is None:
            return []

        return _within_budget(self._index.search_untrusted(query, limit=limit))

    def reindex(self) -> ReindexReport:
        """Brings the index in line with the vault. Blocking; callers on the event loop use a thread."""
        if self._root is None:
            return ReindexReport(indexed=0, skipped=0, removed=0)

        report = self._index.reindex(self._root)
        if report.indexed or report.removed:
            self._log(
                f"memory: {report.indexed} indexed, {report.skipped} unchanged, {report.removed} removed"
            )

        return report

    def status(self) -> MemoryStatus:
        index = self._index.status()

        return MemoryStatus(
            enabled=self.enabled,
            vault=str(self._root) if self._root else None,
            notes=index.notes,
            chunks=index.chunks,
            embedded_chunks=index.embedded_chunks,
            last_indexed_at=index.last_indexed_at,
            embeddings_available=index.embeddings_available,
        )


def _within_budget[ChunkType: (TrustedChunk, UntrustedChunk)](chunks: list[ChunkType]) -> list[ChunkType]:
    """Keeps whole chunks until the budget runs out.

    Whole ones rather than a truncated tail: a chunk cut mid-sentence reads as though the note said
    something it did not, and the reader would cite it that way.
    """
    kept: list[ChunkType] = []
    spent = 0

    for chunk in chunks:
        if spent + len(chunk.text) > MAX_CONTEXT_CHARS:
            break

        kept.append(chunk)
        spent += len(chunk.text)

    return kept
