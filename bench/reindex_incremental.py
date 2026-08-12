"""M4.5 - incremental reindex touches only changed files, measured rather than asserted.

The exit criterion says **measured**, and the distinction matters: a test asserting
``report.indexed == 1`` proves the counter says one, not that the second scan is cheaper than the
first. A rescan that re-read and re-embedded every note would satisfy such a test while taking the
same time as a cold build.

Method: build a vault of N notes, index it cold, then touch exactly one note and index again.
Report both the counts and the wall time. The counts say what it claimed to do; the time says
whether it did it.

**Embeddings are the thing this protects.** Keyword indexing a small vault is fast whatever it
re-reads. Embedding is a model call per chunk, so re-embedding unchanged notes is where a broken
incremental path actually costs - which is why this runs twice, once with an embedding provider and
once without, and reports them separately. Only the embedded figure is meaningful for the criterion.

Needs no running stack: the index is a library and the vault is temporary. If Ollama is not
answering, the embedded pass is skipped and says so rather than reporting a keyword-only number as
though it covered the criterion.

Run:
    uv run python bench/reindex_incremental.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

from _harness import Report  # noqa: E402

from local_zero_brain.llm.ollama import OllamaProvider  # noqa: E402
from local_zero_brain.memory.index import MemoryIndex  # noqa: E402

#: Enough that re-reading everything is visible against re-reading one, small enough that a cold
#: embedded build does not take minutes on a local model.
NOTE_COUNT = 40

#: Long enough to chunk into more than one piece, so the embedded pass has real work per note.
BODY = (
    "This note exists to give the indexer something to read. It describes a piece of the system in "
    "enough words that the chunker produces more than one chunk, because a note that fits in a "
    "single chunk understates the cost of re-embedding a note that does not.\n\n"
    "## Detail\n\n"
    "The second section repeats the shape of the first without repeating its wording, so that "
    "full-text search has distinct terms to index and the vector pass has distinct meaning to "
    "embed. Neither pass is being measured for quality here - only for whether it ran again."
)


def build_vault(root: Path, count: int) -> None:
    knowledge = root / "Knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (knowledge / f"note-{index:03d}.md").write_text(
            f"---\ntitle: Note {index}\n---\n\n# Note {index}\n\n{BODY}\n", encoding="utf-8"
        )


def measure(root: Path, store: Path, provider: object | None) -> dict:
    """A cold build, then a rescan after one note changes."""
    store.unlink(missing_ok=True)
    index = MemoryIndex(store, provider=provider, log=lambda _: None)

    started = time.perf_counter()
    cold = index.reindex(root)
    cold_s = time.perf_counter() - started

    # Touch one note. Content changes, not just mtime: a scanner keyed on size alone would miss a
    # same-length edit, and this is the shape of edit a person actually makes.
    target = root / "Knowledge" / "note-000.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("Note 0", "Note 0 revised"), encoding="utf-8"
    )

    started = time.perf_counter()
    warm = index.reindex(root)
    warm_s = time.perf_counter() - started

    return {
        "cold": {"indexed": cold.indexed, "skipped": cold.skipped, "removed": cold.removed,
                 "seconds": round(cold_s, 3)},
        "warm": {"indexed": warm.indexed, "skipped": warm.skipped, "removed": warm.removed,
                 "seconds": round(warm_s, 3)},
        "speedup": round(cold_s / warm_s, 1) if warm_s > 0 else None,
        "embeddings_available": index.status().embeddings_available,
        "embedded_chunks": index.status().embedded_chunks,
    }


def ollama_answers() -> bool:
    try:
        OllamaProvider().embed(["probe"])
    except Exception:  # noqa: BLE001 - any failure means the embedded pass cannot be measured
        return False
    return True


def main() -> int:
    report = Report(
        metric="reindex-incremental",
        script="bench/reindex_incremental.py",
        method=(
            f"Build a {NOTE_COUNT}-note vault, index cold, edit one note, index again. "
            "Report counts and wall time for each pass, with and without embeddings."
        ),
    )

    workspace = Path(tempfile.mkdtemp(prefix="lz-reindex-bench-"))
    try:
        root = workspace / "vault"
        build_vault(root, NOTE_COUNT)
        print(f"vault: {NOTE_COUNT} notes at {root}")

        print("\nkeyword only (no embedding provider):")
        keyword = measure(root, workspace / "keyword.sqlite", None)
        print(f"  cold: {keyword['cold']}")
        print(f"  warm: {keyword['warm']}")
        report.values["keyword_only"] = keyword

        if ollama_answers():
            print("\nwith embeddings (the pass that matters):")
            embedded = measure(root, workspace / "embedded.sqlite", OllamaProvider())
            print(f"  cold: {embedded['cold']}")
            print(f"  warm: {embedded['warm']}")
            print(f"  speedup: {embedded['speedup']}x")
            report.values["embedded"] = embedded

            if embedded["warm"]["indexed"] != 1:
                report.note(
                    f"rescan reported {embedded['warm']['indexed']} notes indexed, expected 1 - "
                    "the incremental path is re-reading files that did not change"
                )
            if not embedded["embeddings_available"]:
                report.note("embeddings reported unavailable despite the probe answering")
        else:
            report.note(
                "Ollama did not answer, so only the keyword pass was measured. The criterion is "
                "about not re-embedding unchanged notes, and this run does not cover it."
            )

        if keyword["warm"]["indexed"] != 1 or keyword["warm"]["skipped"] != NOTE_COUNT - 1:
            report.note(
                f"keyword rescan indexed {keyword['warm']['indexed']} and skipped "
                f"{keyword['warm']['skipped']}, expected 1 and {NOTE_COUNT - 1}"
            )

        report.write()
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
