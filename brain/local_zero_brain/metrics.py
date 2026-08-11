"""Counters for messages the brain refused to act on.

The contract requires that a failed message is dropped *and counted*, never partially applied. A
silent drop is indistinguishable from a peer that never sent anything, which is exactly the failure
this project is trying not to have: something stops working and nothing says so.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class DropSnapshot:
    """An immutable reading of the counters, safe to hand to a caller or serialize."""

    schema_violations: int = 0
    unsupported_versions: int = 0
    handshake_required: int = 0
    oversized_lines: int = 0
    undecodable_lines: int = 0
    #: Samples discarded because the consumer was not keeping up. The consumer sees these as a gap
    #: in ``seq``, which the contract defines as the way to say samples were lost.
    backpressure_drops: int = 0

    @property
    def total(self) -> int:
        return (
            self.schema_violations
            + self.unsupported_versions
            + self.handshake_required
            + self.oversized_lines
            + self.undecodable_lines
            + self.backpressure_drops
        )


class DropCounters:
    """Mutable counters behind a lock.

    The reader thread and the event loop both touch these, so increments are guarded. Readers get
    a :class:`DropSnapshot` rather than the live object - nothing outside this class can observe a
    half-updated set of numbers, and nothing outside it can quietly reset one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = DropSnapshot()

    def snapshot(self) -> DropSnapshot:
        with self._lock:
            return self._snapshot

    def record_schema_violation(self) -> None:
        self._increment("schema_violations")

    def record_unsupported_version(self) -> None:
        self._increment("unsupported_versions")

    def record_handshake_required(self) -> None:
        self._increment("handshake_required")

    def record_oversized_line(self) -> None:
        self._increment("oversized_lines")

    def record_undecodable_line(self) -> None:
        self._increment("undecodable_lines")

    def record_backpressure_drop(self) -> None:
        self._increment("backpressure_drops")

    def _increment(self, field: str) -> None:
        with self._lock:
            # Replaced, not mutated: DropSnapshot is frozen, so a caller already holding one keeps
            # a stable reading rather than watching it change underneath them.
            self._snapshot = replace(self._snapshot, **{field: getattr(self._snapshot, field) + 1})
