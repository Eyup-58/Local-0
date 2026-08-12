"""The vault index: one sqlite file, rebuilt incrementally, read through two typed doors.

The vault is the source of truth. This is a derived cache and nothing else - deleting the file costs
a rescan and no information, which is the property that lets the user edit their notes in Obsidian
and expect Local Zero to agree with them.

**Two search functions, two return types, no flag.** ``search_trusted`` returns ``TrustedChunk`` and
``search_untrusted`` returns ``UntrustedChunk``. docs/SECURITY.md section 2 invariant 2 asks for
separate functions; the reason it is worth the duplication is that a ``search(trusted=False)`` has a
default, and a default is a thing that gets left off in a call somebody adds a year later, in a diff
where nothing looks wrong.

**FTS5 is the ranking, embeddings are an optional second opinion.** Full-text search ships working;
the vector path is written and switches itself off when no embedding model answers, recording that
in ``status()`` rather than quietly returning worse results. On this machine today it is off:
`nomic-embed-text` is not installed and `gemma4:26b` reports no embedding capability.

The MATCH expression is built from tokens rather than from the query string. FTS5 has a real
expression grammar - quotes, ``NEAR``, column filters, ``*`` - and a query here can be derived from a
model's own words, which makes raw interpolation both a crash and an injection surface.
"""

from __future__ import annotations

import math
import sqlite3
import re
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from local_zero_brain.llm.provider import Provider
from local_zero_brain.memory.chunks import TrustedChunk, UntrustedChunk
from local_zero_brain.memory.vault import chunk_body, iter_notes, parse_note, trust_of

MEMORY_FILE_NAME = "memory.sqlite"

DEFAULT_LIMIT = 5

#: How many keyword candidates are reranked when embeddings are available.
CANDIDATE_POOL = 50

_TOKEN = re.compile(r"[A-Za-z0-9_]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    size       INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL,
    heading    TEXT,
    text       TEXT NOT NULL,
    trusted    INTEGER NOT NULL,
    written_at TEXT NOT NULL,
    embedding  BLOB
);

CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks (path);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
"""


def _noop(_: str) -> None:
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReindexReport:
    """What a scan actually did. Counts rather than prose, so a bench can assert on them."""

    indexed: int
    skipped: int
    removed: int


@dataclass(frozen=True, slots=True)
class IndexStatus:
    notes: int
    chunks: int
    embedded_chunks: int
    last_indexed_at: str | None
    #: False means keyword-only. It is reported rather than inferred, because search that silently
    #: gets worse is the failure nobody notices.
    embeddings_available: bool


class MemoryIndex:
    """Keeps the vault's text searchable, and keeps the two trust levels apart."""

    __slots__ = (
        "_path",
        "_provider",
        "_log",
        "_embeddings_available",
        "_embedding_failure_logged",
        "_schema_ready",
    )

    def __init__(
        self,
        path: Path,
        *,
        provider: Provider | None = None,
        log: Callable[[str], None] = _noop,
    ) -> None:
        self._path = path
        self._provider = provider
        self._log = log
        #: Nothing is claimed until an embedding actually comes back.
        self._embeddings_available = False
        self._embedding_failure_logged = False
        #: The database is created on first use, not on construction.
        #:
        #: ``ws/server.py`` builds an app at import time so uvicorn can find it, and a constructor
        #: that created files would put a database in the user's profile merely because something
        #: imported the module - including a test run that never touches memory. Same rule
        #: trust.py follows: asking is not deciding.
        self._schema_ready = False

    @staticmethod
    def default_path() -> Path:
        from local_zero_brain.capabilities.paths import workspace_root

        return workspace_root().parent / MEMORY_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    # --- writing ---------------------------------------------------------------------------------

    def reindex(self, root: Path) -> ReindexReport:
        """Brings the index in line with the vault, touching only what changed.

        An absent vault is not an error: it means the user renamed a folder or unplugged a drive, and
        the rest of the product does not depend on memory.
        """
        if not root.is_dir():
            return ReindexReport(indexed=0, skipped=0, removed=0)

        indexed = skipped = 0
        seen: set[str] = set()

        with self._connect() as connection:
            known = {row[0]: (row[1], row[2]) for row in connection.execute("SELECT path, mtime, size FROM notes")}

            for note in iter_notes(root):
                relative = note.relative_to(root).as_posix()
                seen.add(relative)
                stat = note.stat()

                if known.get(relative) == (stat.st_mtime, stat.st_size):
                    skipped += 1
                    continue

                self._index_note(connection, root, note, relative, stat.st_mtime, stat.st_size)
                indexed += 1

            removed = 0
            for gone in set(known) - seen:
                self._forget_note(connection, gone)
                removed += 1

        return ReindexReport(indexed=indexed, skipped=skipped, removed=removed)

    def _index_note(
        self,
        connection: sqlite3.Connection,
        root: Path,
        note: Path,
        relative: str,
        mtime: float,
        size: int,
    ) -> None:
        try:
            text = note.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            # One unreadable note must not stop the scan. It is reported and left out - which is
            # visible in the counts, unlike a silently empty index.
            self._log(f"skipped {relative}: {type(error).__name__}")
            return

        metadata, body = parse_note(text, log=lambda message: self._log(f"{relative}: {message}"))
        trust = trust_of(note.relative_to(root), metadata)
        written_at = datetime.fromtimestamp(mtime, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        self._forget_note(connection, relative)

        pieces = chunk_body(body)
        vectors = self._embed([text for _, text in pieces])

        for (heading, chunk_text), vector in zip(pieces, vectors, strict=True):
            cursor = connection.execute(
                "INSERT INTO chunks (path, heading, text, trusted, written_at, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (relative, heading, chunk_text, 1 if trust == "trusted" else 0, written_at, vector),
            )
            connection.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cursor.lastrowid, chunk_text)
            )

        connection.execute(
            "INSERT INTO notes (path, mtime, size, indexed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size, "
            "indexed_at = excluded.indexed_at",
            (relative, mtime, size, _now()),
        )

    def _forget_note(self, connection: sqlite3.Connection, relative: str) -> None:
        connection.execute(
            "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)", (relative,)
        )
        connection.execute("DELETE FROM chunks WHERE path = ?", (relative,))
        connection.execute("DELETE FROM notes WHERE path = ?", (relative,))

    # --- reading ---------------------------------------------------------------------------------

    def search_trusted(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[TrustedChunk]:
        """Text the user wrote themselves. The only kind the planner may see."""
        return [
            TrustedChunk(note_path=row[0], text=row[2], written_at=row[3])
            for row in self._search(query, trusted=1, limit=limit)
        ]

    def search_untrusted(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[UntrustedChunk]:
        """Everything else, including anything Local Zero wrote. Reaches the reader and the user."""
        return [
            UntrustedChunk(source=row[0], text=row[2], fetched_at=row[3])
            for row in self._search(query, trusted=0, limit=limit)
        ]

    def status(self) -> IndexStatus:
        with self._connect() as connection:
            notes = connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            embedded = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
            ).fetchone()[0]
            last = connection.execute("SELECT MAX(indexed_at) FROM notes").fetchone()[0]

        return IndexStatus(
            notes=notes,
            chunks=chunks,
            embedded_chunks=embedded,
            last_indexed_at=last,
            embeddings_available=self._embeddings_available,
        )

    def _search(self, query: str, *, trusted: int, limit: int) -> list[tuple]:
        expression = _match_expression(query)
        if expression is None:
            # An empty query matching everything would turn "recall" into "inject the whole vault".
            return []

        pool = max(limit, CANDIDATE_POOL) if self._embeddings_available else limit

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT c.path, c.heading, c.text, c.written_at, c.embedding "
                "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                "WHERE chunks_fts MATCH ? AND c.trusted = ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (expression, trusted, pool),
            ).fetchall()

        if not self._embeddings_available or not rows:
            return rows[:limit]

        return self._rerank(query, rows)[:limit]

    def _rerank(self, query: str, rows: Sequence[tuple]) -> list[tuple]:
        """Reorders keyword hits by vector similarity.

        Deliberately a rerank rather than a scan of every chunk: recall stays bounded by what the
        keyword pass found, which is the honest limitation to state rather than to imply otherwise.
        A full vector scan is the upgrade if the vault ever grows enough for that to matter.
        """
        vectors = self._embed([query])
        if vectors[0] is None:
            return list(rows)

        target = _to_floats(vectors[0])

        def score(row: tuple) -> float:
            return -_cosine(target, _to_floats(row[4])) if row[4] is not None else 0.0

        return sorted(rows, key=score)

    # --- embeddings ------------------------------------------------------------------------------

    def _embed(self, texts: Sequence[str]) -> list[bytes | None]:
        """Vectors for these texts, or a list of Nones when no embedding model answers."""
        if self._provider is None or not texts:
            return [None] * len(texts)

        try:
            vectors = self._provider.embed(texts)
        except Exception as error:  # noqa: BLE001 - any provider failure means keyword-only
            if not self._embedding_failure_logged:
                # Once. A missing model is one fact, and logged per chunk it becomes a wall of text
                # that hides whatever else went wrong in the same run.
                self._log(
                    f"embeddings are unavailable ({type(error).__name__}); memory search is "
                    "keyword-only until an embedding model is installed"
                )
                self._embedding_failure_logged = True

            self._embeddings_available = False
            return [None] * len(texts)

        self._embeddings_available = True
        return [array("f", vector).tobytes() for vector in vectors]

    def _connect(self) -> sqlite3.Connection:
        if not self._schema_ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path)
            with connection:
                connection.executescript(_SCHEMA)
            connection.close()
            self._schema_ready = True

        return sqlite3.connect(self._path)


def _match_expression(query: str) -> str | None:
    """Builds a MATCH expression from the query's word characters, and nothing else.

    Everything outside ``[A-Za-z0-9_]`` is discarded rather than escaped. Escaping keeps the
    attacker's characters in the string and bets on getting the rules right; discarding does not.
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None

    return " OR ".join(f'"{token}"' for token in tokens)


def _to_floats(blob: bytes) -> array:
    vector = array("f")
    vector.frombytes(blob)
    return vector


def _cosine(left: array, right: array) -> float:
    if len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    magnitude = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))

    return dot / magnitude if magnitude else 0.0
