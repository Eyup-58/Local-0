"""The pending-approval queue and the rejection memory.

Two jobs, both about an answer meaning exactly one thing:

* a ``request_id`` the brain issued can be answered **once**. A decision replayed against an id that
  has already been settled must not re-authorise anything - otherwise a captured or duplicated frame
  is a second execution.
* a rejected invocation is not re-attempted in the same session (docs/SECURITY.md section 5, and an
  M3 exit criterion). Without that, "no" means "ask again", and a model that can ask twice can ask a
  hundred times.
"""

from __future__ import annotations

from local_zero_brain.approvals import ApprovalQueue, RejectionMemory


def open_one(queue: ApprovalQueue, capability: str = "delete_file", **overrides: object):
    payload: dict[str, object] = {
        "capability": capability,
        "resolved_args": {"path": "C:/workspace/old.log"},
        "affected_paths": ("C:/workspace/old.log",),
        "side_effect": "destructive",
        "origin": "user_direct",
    }
    payload.update(overrides)
    return queue.open(**payload)  # type: ignore[arg-type]


# --- the queue ---------------------------------------------------------------------------------


def test_opening_a_request_returns_it_with_an_id() -> None:
    queue = ApprovalQueue()

    pending = open_one(queue)

    assert pending.request_id
    assert pending.capability == "delete_file"
    assert queue.get(pending.request_id) is pending


def test_each_request_gets_a_distinct_id() -> None:
    """Two identical operations are two decisions. Sharing an id would let one answer settle both."""
    queue = ApprovalQueue()

    first = open_one(queue)
    second = open_one(queue)

    assert first.request_id != second.request_id


def test_an_unknown_id_resolves_to_nothing() -> None:
    """An id the brain never issued identifies nothing. Not an error - a refusal."""
    queue = ApprovalQueue()

    assert queue.resolve("11111111-2222-4333-8444-555555555555", approved=True) is None


def test_resolving_removes_the_request() -> None:
    queue = ApprovalQueue()
    pending = open_one(queue)

    queue.resolve(pending.request_id, approved=True)

    assert queue.get(pending.request_id) is None


def test_a_request_cannot_be_resolved_twice() -> None:
    """The property that makes a replayed decision harmless.

    A duplicated approval.decision frame - captured, retried by a reconnecting client, or sent twice
    by a confused one - must not authorise a second execution.
    """
    queue = ApprovalQueue()
    pending = open_one(queue)

    first = queue.resolve(pending.request_id, approved=True)
    second = queue.resolve(pending.request_id, approved=True)

    assert first is not None
    assert second is None


def test_rejecting_also_settles_the_request() -> None:
    queue = ApprovalQueue()
    pending = open_one(queue)

    queue.resolve(pending.request_id, approved=False)

    assert queue.get(pending.request_id) is None


def test_pending_requests_are_countable() -> None:
    queue = ApprovalQueue()
    open_one(queue)
    open_one(queue)

    assert len(queue) == 2


def test_a_pending_request_is_immutable() -> None:
    queue = ApprovalQueue()
    pending = open_one(queue)

    try:
        pending.capability = "read_text_file"  # type: ignore[misc]
    except Exception:
        return

    raise AssertionError("a pending approval must be frozen: it is what the user was shown")


# --- the rejection memory ----------------------------------------------------------------------


def test_an_unrejected_invocation_is_not_remembered() -> None:
    memory = RejectionMemory()

    assert memory.was_rejected("delete_file", "abc123") is False


def test_a_rejected_invocation_is_remembered() -> None:
    memory = RejectionMemory()

    memory.remember("delete_file", "abc123")

    assert memory.was_rejected("delete_file", "abc123") is True


def test_a_different_argument_hash_is_a_different_invocation() -> None:
    """Rejecting one deletion does not reject every deletion. The user said no to a specific thing."""
    memory = RejectionMemory()
    memory.remember("delete_file", "abc123")

    assert memory.was_rejected("delete_file", "def456") is False


def test_the_same_hash_under_another_capability_is_not_confused() -> None:
    """args_hash is only unique within a capability - two capabilities can take identical arguments,
    and 'delete this path' being refused says nothing about 'read this path'."""
    memory = RejectionMemory()
    memory.remember("delete_file", "abc123")

    assert memory.was_rejected("read_text_file", "abc123") is False


def test_remembering_twice_is_harmless() -> None:
    memory = RejectionMemory()

    memory.remember("delete_file", "abc123")
    memory.remember("delete_file", "abc123")

    assert memory.was_rejected("delete_file", "abc123") is True
