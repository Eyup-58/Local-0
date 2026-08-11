"""Pending approvals, and the memory of what was refused.

docs/SECURITY.md section 5. Two properties carry this module:

**An issued ``request_id`` can be answered once.** ``resolve`` removes the request as it settles it,
so a second decision against the same id finds nothing. That is what makes a replayed
``approval.decision`` harmless - a frame captured, retried by a reconnecting client, or simply sent
twice must not authorise a second execution.

**A rejection is remembered for the session.** Without that, "no" means "ask again", and something
that can ask twice can ask a hundred times until the human clicks the wrong button. The memory is
keyed on the capability *and* the argument hash, because the two are only meaningful together:
refusing one deletion is not refusing every deletion, and refusing to delete a path says nothing
about reading it.

Deliberately in-memory. A rejection lasting beyond the session would need its own expiry story and a
way to review it, and neither exists yet - `SECURITY.md` says "in the same session" and this matches
that exactly rather than inventing more.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """One invocation waiting on a human.

    Frozen because this is precisely what the user was shown. If any field could change between the
    dialog rendering and the decision arriving, the thing approved would not be the thing seen.
    """

    request_id: str
    capability: str
    #: Post-validation, post-canonicalisation. What will run, not what was asked for.
    resolved_args: Mapping[str, object]
    affected_paths: tuple[str, ...]
    side_effect: str
    origin: str


class ApprovalQueue:
    """The set of requests currently awaiting an answer."""

    __slots__ = ("_pending", "_lock")

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def open(
        self,
        *,
        capability: str,
        resolved_args: Mapping[str, object],
        affected_paths: Sequence[str],
        side_effect: str,
        origin: str,
    ) -> PendingApproval:
        """Registers a request and returns it, with an id the brain generated.

        The id comes from here rather than from a caller: an identifier something else chose is an
        identifier something else can predict, and predicting one is the first half of answering a
        request that was never asked.
        """
        pending = PendingApproval(
            request_id=str(uuid.uuid4()),
            capability=capability,
            resolved_args=dict(resolved_args),
            affected_paths=tuple(affected_paths),
            side_effect=side_effect,
            origin=origin,
        )

        with self._lock:
            self._pending[pending.request_id] = pending

        return pending

    def get(self, request_id: str) -> PendingApproval | None:
        with self._lock:
            return self._pending.get(request_id)

    def resolve(self, request_id: str, *, approved: bool) -> PendingApproval | None:
        """Settles a request and hands back what was settled, or None if there was nothing.

        ``approved`` is not stored: this returns the request so the caller can act on it, and a
        settled request is gone. Keeping resolved requests around would mean deciding how long to
        keep them and what a second decision against one means, and "it is gone" answers both.
        """
        with self._lock:
            return self._pending.pop(request_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


@dataclass
class RejectionMemory:
    """What the user has already said no to, for as long as this session lasts."""

    _refused: set[tuple[str, str]] = field(default_factory=set)

    def remember(self, capability: str, args_hash: str) -> None:
        self._refused.add((capability, args_hash))

    def was_rejected(self, capability: str, args_hash: str) -> bool:
        return (capability, args_hash) in self._refused
