"""Where untrusted text lives: its own database file, outside the vault entirely.

docs/SECURITY.md section 2, invariant 1: untrusted content lives in a **separate table with a
separate index** - not a ``trust`` column on a shared table, because a column can be forgotten in a
``WHERE`` clause and one forgotten ``WHERE`` clause is the whole exploit.

This goes one further than the invariant asks, by the user's decision when the vault was chosen as
the trusted store: the two namespaces are separate *technologies* in separate locations. Trusted
text is markdown a human wrote in the vault; untrusted text is rows in
``%LOCALAPPDATA%\\LocalZero\\untrusted.sqlite``. There is no query that could return both by
accident, because there is no query language that spans them.

There is exactly one retrieval method and it is named for what it returns. No ``retrieve(trusted=
False)``: a flag is a thing that gets defaulted wrong once, in a call somebody adds later, and the
mistake is invisible at the call site.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from local_zero_brain.memory.chunks import UntrustedChunk

#: Sits beside the workspace, like trust.json - never inside it, so no capability's allowed_roots
#: can reach it.
UNTRUSTED_FILE_NAME = "untrusted.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS untrusted_chunks (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    text       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
)
"""

DEFAULT_LIMIT = 5


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UntrustedStore:
    """The untrusted namespace. Everything it returns is an ``UntrustedChunk``, always."""

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.execute(_SCHEMA)

    @staticmethod
    def default_path() -> Path:
        from local_zero_brain.capabilities.paths import workspace_root

        return workspace_root().parent / UNTRUSTED_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def remember(self, *, source: str, text: str) -> UntrustedChunk:
        """Stores text and stamps it untrusted. There is no other way in, and no way to stamp it
        anything else."""
        chunk = UntrustedChunk(source=source, text=text, fetched_at=_now())

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO untrusted_chunks (source, text, fetched_at) VALUES (?, ?, ?)",
                (chunk.source, chunk.text, chunk.fetched_at),
            )

        return chunk

    def retrieve_untrusted(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[UntrustedChunk]:
        """Substring match, newest first.

        Deliberately unsophisticated: ranking untrusted text better is M4.5's problem, and until
        something reads these chunks for a user, a smarter search would be an optimisation of
        nothing. Parameterised rather than interpolated - the query string is untrusted input by
        definition, since it can be derived from a model's own words.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source, text, fetched_at FROM untrusted_chunks "
                "WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()

        return [UntrustedChunk(source=row[0], text=row[1], fetched_at=row[2]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)
