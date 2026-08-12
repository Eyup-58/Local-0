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
in ``status()`` rather than quietly returning worse results.

That switch earned itself on 2026-08-12. `nomic-embed-text` was not installed, so search ran on
keywords alone and said so in ``status()`` the whole time - degraded visibly rather than silently.
Once the model was pulled, the same code embedded every chunk with no change here, and recall began
matching questions that share no words with the note answering them.

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
from typing import TypeVar

from local_zero_brain.llm.provider import Provider
from local_zero_brain.memory.chunks import TrustedChunk, UntrustedChunk
from local_zero_brain.memory.vault import RETIRED_STATUSES, chunk_body, iter_notes, parse_note, trust_of

MEMORY_FILE_NAME = "memory.sqlite"

DEFAULT_LIMIT = 5

#: How many candidates each pass contributes before fusion.
CANDIDATE_POOL = 50

#: The usual reciprocal-rank-fusion constant. It damps the influence of the very top rank so one
#: pass cannot dominate the other on a single confident hit.
_RRF_K = 60

#: How close a chunk must be before the vector pass will offer it.
#:
#: A scan always has a nearest neighbour, so without a floor every question recalls something -
#: measured: asking about submarines returned both notes in a two-note vault, and that text would
#: have gone into the prompt. Measured with `nomic-embed-text` on 2026-08-12:
#:
#:     graphics card -> card.md   0.790      submarine        -> editor.md  0.414
#:     video card    -> card.md   0.747      quantum chromo.  -> card.md    0.421
#:     GPU           -> card.md   0.721      the price of tea -> card.md    0.405
#:     dark theme    -> editor.md 0.563      recipe for bread -> card.md    0.375
#:     my screen     -> editor.md 0.502      submarine        -> card.md    0.337
#:
#: Genuine hits bottom out at 0.50; unrelated questions top out at 0.42. 0.45 sits between them with
#: margin on both sides. **It is specific to this embedding model** - an index built with a
#: different one is not comparable anyway, and this number would need measuring again.
VECTOR_FLOOR = 0.45

_TOKEN = re.compile(r"[A-Za-z0-9_]+")

#: Bumped whenever the tables change. The file is a derived cache, so a mismatch is answered by
#: rebuilding it from the vault rather than by migration code - there is nothing in here that the
#: vault does not already hold, and migrations for a cache are a maintenance cost with no payoff.
SCHEMA_VERSION = 2

#: A word has to be at least this long to count towards two notes being about the same thing.
#: Shorter ones are mostly grammar, and grammar overlaps everywhere.
MIN_TOKEN_LENGTH = 4

#: How many significant words two notes must share before they are worth a human's attention.
MIN_SHARED_TOKENS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    size       INTEGER NOT NULL,
    indexed_at TEXT NOT NULL,
    trusted    INTEGER NOT NULL DEFAULT 0,
    -- What the note's own frontmatter says. Kept separate from `superseded` below, which is derived
    -- from *other* notes: merging them would mean recomputing the derived one clobbers the
    -- declared one, and a note the user marked superseded would quietly come back.
    status     TEXT NOT NULL DEFAULT 'active',
    superseded INTEGER NOT NULL DEFAULT 0,
    note_type  TEXT,
    supersedes TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL,
    heading    TEXT,
    text       TEXT NOT NULL,
    written_at TEXT NOT NULL,
    embedding  BLOB
);

CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks (path);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
"""

_DROP = """
DROP TABLE IF EXISTS chunks_fts;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS notes;
"""

_T = TypeVar("_T")


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
        "_unusable",
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
        #: Set only when a corrupt cache could not be discarded and rebuilt either. Memory then
        #: answers empty for the rest of the session; nothing else in the product depends on it.
        self._unusable = False

    @staticmethod
    def default_path() -> Path:
        from local_zero_brain.capabilities.paths import workspace_root

        return workspace_root().parent / MEMORY_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    @property
    def provider(self) -> Provider | None:
        """Whatever computes the vectors. Exposed so the wiring can be asserted rather than assumed.

        docs/SECURITY.md section 11 requires this to be the local provider in **both** modes, and
        that is a property of how ``create_app`` builds the index - not of anything this class does.
        Without a reader for it, the rule can only be checked by reading the constructor call.
        """
        return self._provider

    # --- writing ---------------------------------------------------------------------------------

    def reindex(self, root: Path) -> ReindexReport:
        """Brings the index in line with the vault, touching only what changed.

        An absent vault is not an error: it means the user renamed a folder or unplugged a drive, and
        the rest of the product does not depend on memory.
        """
        if not root.is_dir():
            return ReindexReport(indexed=0, skipped=0, removed=0)

        def scan(connection: sqlite3.Connection) -> ReindexReport:
            indexed = skipped = 0
            seen: set[str] = set()

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

            self._apply_supersedes(connection)

            return ReindexReport(indexed=indexed, skipped=skipped, removed=removed)

        return self._run(scan, ReindexReport(indexed=0, skipped=0, removed=0))

    def _apply_supersedes(self, connection: sqlite3.Connection) -> None:
        """Retires the notes that other notes have replaced.

        Run over the whole index rather than per note, because the retiring note and the retired one
        are indexed independently and either order is possible.

        **Only a trusted note may retire anything.** Superseding removes a memory from recall, so an
        agent-written note able to do it could quietly retire whatever the user wrote that stood in
        its way - achieving by deletion what it is not permitted to achieve by instruction. A
        dangling reference is ignored: vaults get reorganised, and that is not a reason to fail a
        scan.
        """
        connection.execute("UPDATE notes SET superseded = 0")

        for (target,) in connection.execute(
            "SELECT supersedes FROM notes "
            "WHERE supersedes IS NOT NULL AND trusted = 1 AND status NOT IN (?, ?)",
            tuple(sorted(RETIRED_STATUSES)),
        ).fetchall():
            connection.execute("UPDATE notes SET superseded = 1 WHERE path = ?", (target,))

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
                "INSERT INTO chunks (path, heading, text, written_at, embedding) VALUES (?, ?, ?, ?, ?)",
                (relative, heading, chunk_text, written_at, vector),
            )
            connection.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cursor.lastrowid, chunk_text)
            )

        connection.execute(
            "INSERT INTO notes (path, mtime, size, indexed_at, trusted, status, note_type, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size, "
            "indexed_at = excluded.indexed_at, trusted = excluded.trusted, status = excluded.status, "
            "note_type = excluded.note_type, supersedes = excluded.supersedes",
            (
                relative,
                mtime,
                size,
                _now(),
                1 if trust == "trusted" else 0,
                metadata.status or "active",
                metadata.note_type,
                metadata.supersedes,
            ),
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
        """What the index holds.

        Called during the WebSocket handshake, which is why it answers rather than raises when the
        file is broken: an empty count is the truth about an unreadable cache, and it is the only
        answer that leaves telemetry and approval - neither of which uses memory - still connecting.
        """

        def counts(connection: sqlite3.Connection) -> IndexStatus:
            return IndexStatus(
                notes=connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
                chunks=connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                embedded_chunks=connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
                ).fetchone()[0],
                last_indexed_at=connection.execute("SELECT MAX(indexed_at) FROM notes").fetchone()[0],
                embeddings_available=self._embeddings_available,
            )

        return self._run(
            counts,
            IndexStatus(
                notes=0,
                chunks=0,
                embedded_chunks=0,
                last_indexed_at=None,
                embeddings_available=self._embeddings_available,
            ),
        )

    def conflict_candidates(self, note_path: str) -> list[str]:
        """Other live notes of the same kind that appear to be about the same thing.

        **Candidates, not verdicts.** Deciding which of two contradictory notes is true is a
        judgement, and a keyword heuristic making that judgement silently would be worse than making
        none - it would delete one of the user's memories on the strength of word overlap. Nothing
        here changes any note's status; the caller shows a human.

        Retired notes are excluded: a correction that already superseded something is not a
        contradiction, and reporting it would make every correction look like one.
        """
        retired = tuple(sorted(RETIRED_STATUSES))

        def candidates(connection: sqlite3.Connection) -> list[str]:
            row = connection.execute(
                "SELECT note_type, trusted FROM notes WHERE path = ? AND superseded = 0 "
                f"AND status NOT IN ({', '.join('?' * len(retired))})",
                (note_path, *retired),
            ).fetchone()

            if row is None:
                return []

            note_type, trusted = row
            mine = _significant_tokens(self._text_of(connection, note_path))

            others = connection.execute(
                "SELECT path FROM notes WHERE path != ? AND trusted = ? AND superseded = 0 "
                f"AND status NOT IN ({', '.join('?' * len(retired))}) "
                "AND note_type IS ? ",
                (note_path, trusted, *retired, note_type),
            ).fetchall()

            return [
                path
                for (path,) in others
                if len(mine & _significant_tokens(self._text_of(connection, path))) >= MIN_SHARED_TOKENS
            ]

        return self._run(candidates, [])

    def _text_of(self, connection: sqlite3.Connection, note_path: str) -> str:
        rows = connection.execute("SELECT text FROM chunks WHERE path = ?", (note_path,)).fetchall()
        return "\n".join(row[0] for row in rows)

    def _search(self, query: str, *, trusted: int, limit: int) -> list[tuple]:
        """Keyword hits and vector hits, fused.

        The two passes answer different questions and neither subsumes the other. Keyword search
        finds the note that uses your words; vector search finds the note that means what you meant.
        Reranking keyword hits alone - the first shape of this code - could only ever reorder what
        the words already found, which showed up immediately against a real model: asking for "GPU"
        returned nothing while a note said "graphics card".

        The vector pass is a full scan of this trust level. For a personal vault that is a few
        megabytes of floats and a few milliseconds; if a vault ever grows enough for that to matter,
        this is where an approximate index would go.
        """
        # The vector pass runs first because embedding the query is also the probe.
        #
        # Checking the flag before trying could not work, and did not: it starts False and only a
        # successful embedding sets it, so a process that indexed nothing new this run skipped the
        # vector pass entirely - with every chunk's embedding already sitting in the database. A
        # restarted brain over an unchanged vault silently searched by keyword alone, which is the
        # exact failure the flag exists to make visible. Found by running it.
        vector = self._vector_rows(query, trusted=trusted, limit=limit)
        keyword = self._keyword_rows(query, trusted=trusted, limit=limit)

        if not self._embeddings_available:
            return keyword

        return _fuse(keyword, vector)[:limit]

    def _keyword_rows(self, query: str, *, trusted: int, limit: int) -> list[tuple]:
        expression = _match_expression(query)
        if expression is None:
            # An empty query matching everything would turn "recall" into "inject the whole vault".
            return []

        pool = max(limit, CANDIDATE_POOL) if self._embeddings_available else limit

        def matches(connection: sqlite3.Connection) -> list[tuple]:
            return connection.execute(
                "SELECT c.path, c.heading, c.text, c.written_at, c.embedding "
                "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid JOIN notes n ON n.path = c.path "
                # A retired memory stays in the vault and stays out of recall: asking about the
                # present gets the present, and nothing was destroyed to make that true.
                f"WHERE chunks_fts MATCH ? AND n.trusted = ? AND n.superseded = 0 "
                f"AND n.status NOT IN ({', '.join('?' * len(RETIRED_STATUSES))}) "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (expression, trusted, *sorted(RETIRED_STATUSES), pool),
            ).fetchall()

        return self._run(matches, [])[:pool]

    def _vector_rows(self, query: str, *, trusted: int, limit: int) -> list[tuple]:
        """The chunks closest in meaning, whatever words they used."""
        vectors = self._embed([query])
        if vectors[0] is None:
            return []

        target = _to_floats(vectors[0])
        retired = tuple(sorted(RETIRED_STATUSES))

        def embedded(connection: sqlite3.Connection) -> list[tuple]:
            return connection.execute(
                "SELECT c.path, c.heading, c.text, c.written_at, c.embedding "
                "FROM chunks c JOIN notes n ON n.path = c.path "
                "WHERE c.embedding IS NOT NULL AND n.trusted = ? AND n.superseded = 0 "
                f"AND n.status NOT IN ({', '.join('?' * len(retired))})",
                (trusted, *retired),
            ).fetchall()

        rows = self._run(embedded, [])

        near = [(row, _cosine(target, _to_floats(row[4]))) for row in rows]
        near = [(row, score) for row, score in near if score >= VECTOR_FLOOR]

        return [row for row, _ in sorted(near, key=lambda pair: -pair[1])][: max(limit, CANDIDATE_POOL)]

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

    def _run(self, work: Callable[[sqlite3.Connection], _T], default: _T) -> _T:
        """Runs ``work`` against the index, degrading to ``default`` instead of raising.

        Every read and write in this class goes through here, which is the point: the caller that
        crashed the WebSocket handshake was ``status()``, and guarding that one would have left the
        same failure waiting behind ``search_trusted`` and ``reindex``.

        The two failure shapes are answered differently. A file whose header will not open is
        handled by ``_open``, which discards it and rebuilds - the user loses nothing, because the
        vault is the source of truth. Corruption that only surfaces deeper in, once pages are being
        read, cannot be answered mid-transaction: the file is discarded here so the *next* call
        rebuilds it, and this call returns the empty answer rather than a wrong one.
        """
        connection = self._open()
        if connection is None:
            return default

        try:
            with connection:
                return work(connection)
        except sqlite3.DatabaseError as error:
            connection.close()
            # Closed before discarding, not merely in the `finally` below. Windows refuses to unlink
            # a file that is still open, so discarding from under a live connection turns every
            # mid-flight corruption into a permanent memory-off - the recovery would be unreachable
            # on the one platform this product runs on. `close` is idempotent, so the `finally`
            # calling it again costs nothing.
            self._discard(f"the memory index became unreadable while in use ({error})")
            return default
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection | None:
        """A connection with the schema in place, or None when the file cannot be made usable.

        The recovery is the one this file already applies to a stale schema, one step harder. A
        cache holding nothing the vault does not hold is not worth migration code, and it is not
        worth repair code either: delete it and rescan.
        """
        if self._unusable:
            return None

        try:
            return self._prepare()
        except sqlite3.DatabaseError as error:
            self._discard(f"the memory index at {self._path} is unreadable ({error})")

        if self._unusable:
            return None

        try:
            return self._prepare()
        except sqlite3.DatabaseError as error:
            # A fresh file that still will not open is not a corrupt cache; something is wrong with
            # the location itself. Memory goes off and the rest of the product carries on.
            self._give_up(f"the memory index could not be rebuilt ({error})")
            return None

    def _prepare(self) -> sqlite3.Connection:
        """Opens the file, bringing the schema up to date on first use."""
        if not self._schema_ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path)
            try:
                with connection:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version and version != SCHEMA_VERSION:
                        # A cache from an older shape. Everything in it came from the vault, so the
                        # cheapest correct answer is to throw it away and rescan.
                        self._log(f"memory index schema {version} is stale; rebuilding from the vault")
                        connection.executescript(_DROP)

                    connection.executescript(_SCHEMA)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            finally:
                connection.close()

            self._schema_ready = True

        return sqlite3.connect(self._path)

    def _discard(self, reason: str) -> None:
        """Deletes the cache so the next call rebuilds it from the vault.

        Reported rather than done quietly. Deleting a file the user can see in their own profile is
        not a thing to leave out of the log, even one this product created and can recreate.
        """
        self._log(f"{reason}; discarding it and rebuilding from the vault")

        try:
            self._path.unlink(missing_ok=True)
        except OSError as error:
            # Held open by a backup agent, or on a volume that has gone read-only. Rebuilding is not
            # available either, so there is nothing left to do but keep everything else running.
            self._give_up(f"the memory index could not be discarded ({error})")
            return

        self._schema_ready = False

    def _give_up(self, reason: str) -> None:
        """Memory off, everything else up. Logged once - one broken file is one fact."""
        if not self._unusable:
            self._log(f"{reason}; memory is off for this session and the vault is untouched")

        self._unusable = True


def _match_expression(query: str) -> str | None:
    """Builds a MATCH expression from the query's word characters, and nothing else.

    Everything outside ``[A-Za-z0-9_]`` is discarded rather than escaped. Escaping keeps the
    attacker's characters in the string and bets on getting the rules right; discarding does not.
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None

    return " OR ".join(f'"{token}"' for token in tokens)


def _fuse(*rankings: Sequence[tuple]) -> list[tuple]:
    """Reciprocal rank fusion over the keyword and vector rankings.

    Ranks rather than scores, because bm25 and cosine are not on the same scale and normalising them
    into one would mean inventing a weighting nobody measured. A chunk near the top of either list
    surfaces; a chunk near the top of both surfaces higher.
    """
    scores: dict[str, float] = {}
    rows: dict[str, tuple] = {}

    for ranking in rankings:
        for rank, row in enumerate(ranking):
            key = f"{row[0]}\x00{row[2]}"
            rows[key] = row
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)

    return [rows[key] for key in sorted(scores, key=lambda key: -scores[key])]


def _significant_tokens(text: str) -> set[str]:
    """Words long enough to be about something.

    A length floor rather than a stopword list: a list is a thing that is always missing the word
    this vault happens to use, and the floor costs nothing to maintain.
    """
    return {token.lower() for token in _TOKEN.findall(text) if len(token) >= MIN_TOKEN_LENGTH}


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
