"""Append-only record of every guard decision, denials included.

docs/SECURITY.md section 9. Two properties do the work:

**The arguments are hashed, never written.** They carry paths and file content, and a log is the
thing people paste into an issue when something goes wrong. ``args_hash`` still makes two records of
the same operation comparable, which is what makes a repeated attempt visible without the log
becoming a copy of what was attempted.

**Allowed invocations are recorded before they execute**, so a crash mid-operation still leaves a
record. A log written afterwards is a log that is missing exactly the entries someone would most want
to read.

``logs/`` is gitignored: once ingestion exists this file will contain attacker-controlled strings,
and committing it would publish them and dress them as repository content.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Origin = Literal["user_direct", "untrusted_content"]
Decision = Literal["allowed", "denied_guard", "denied_user", "denied_origin"]


def hash_arguments(resolved_args: Mapping[str, object]) -> str:
    """SHA-256 over the canonical form of the resolved arguments.

    Keys are sorted so that the same operation hashes the same however the mapping was built;
    without that, two records of one attempt are not comparable and the log stops being useful for
    spotting a repeat.
    """
    canonical = json.dumps(resolved_args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One decision. ``resolved_args`` is hashed on the way out and never serialized."""

    origin: Origin
    capability: str
    resolved_args: Mapping[str, object]
    #: Computed by the backend, not narrated by anything. This is the list a human is shown when
    #: approving in M3, so it is the one place a path is written in full.
    affected_paths: Sequence[str]
    side_effect: str
    decision: Decision
    reason: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    def to_entry(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "origin": self.origin,
            "capability": self.capability,
            "args_hash": hash_arguments(self.resolved_args),
            "affected_paths": [str(path) for path in self.affected_paths],
            "side_effect": self.side_effect,
            "decision": self.decision,
            "reason": self.reason,
        }


class AuditLog:
    """One JSON object per line, opened for append every time.

    Reopening per record rather than holding a handle is deliberate: nothing keeps a writable handle
    to the audit trail between decisions, and an append on Windows is atomic for writes below the
    pipe buffer, so concurrent writers interleave lines rather than corrupting them.
    """

    __slots__ = ("_path", "_lock")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, record: AuditRecord) -> None:
        line = json.dumps(record.to_entry(), separators=(",", ":"))

        with self._lock:
            # logs/ is gitignored, so a fresh clone does not have it. A missing directory must not
            # be the reason a denial goes unrecorded.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
