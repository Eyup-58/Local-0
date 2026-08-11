"""The guard chain, in the order docs/SECURITY.md section 4 fixes it.

Each step gets a test proving it denies what it is supposed to deny, per ROADMAP M2. The one that
carries the most weight is the argument test: *a whitelist that only checks the name is not a
control*, and knowing that ``read_text_file`` is registered says nothing about whether
``..\\..\\Windows\\System32\\config\\SAM`` is an acceptable argument to it.

M2 has no approval flow - that is M3 - so the approver here denies everything with a stated reason.
That is the honest way to satisfy "destructive cannot execute without approval": it cannot, because
approving is not yet a thing that can happen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_zero_brain.audit import AuditLog
from local_zero_brain.capabilities.guard import (
    Allowed,
    Denied,
    Guard,
    Invocation,
    Pending,
)
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.trust import TrustStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("hello", encoding="utf-8")
    return root


@pytest.fixture
def guard(workspace: Path, tmp_path: Path) -> Guard:
    return Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
    )


def invoke(capability: str, origin: str = "user_direct", **args: object) -> Invocation:
    return Invocation(capability=capability, args=args, origin=origin)


@pytest.fixture
def trusting_guard(workspace: Path, tmp_path: Path) -> Guard:
    """A guard with the trust button on.

    This is the user's chosen configuration: approval bypassed for every invocation regardless of
    side_effect or origin. Used here where a test needs the chain to get past step 4 - and, in the
    trust-mode section below, as the thing under test.
    """
    store = TrustStore(tmp_path / "trust.json")
    store.set(enabled=True)

    return Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        trust=store,
    )


# --- the three capabilities actually do what they claim ----------------------------------------
#
# ROADMAP M2's first criterion is that three example capabilities *work*, one per side_effect. The
# guard deciding "allowed" is not that: it returns a verdict and an executor runs the handler. These
# run the handler from the verdict, which is the only thing that makes the criterion true.


def test_the_read_capability_returns_the_file_contents(guard: Guard, workspace: Path) -> None:
    verdict = guard.evaluate(invoke("read_text_file", path=str(workspace / "note.txt")))

    assert isinstance(verdict, Allowed)
    assert verdict.capability.handler(**verdict.resolved_args) == "hello"


def test_the_write_capability_creates_the_file(trusting_guard: Guard, workspace: Path) -> None:
    target = workspace / "written.txt"

    verdict = trusting_guard.evaluate(
        invoke("write_text_file", path=str(target), content="written by a capability")
    )

    assert isinstance(verdict, Allowed)
    verdict.capability.handler(**verdict.resolved_args)
    assert target.read_text(encoding="utf-8") == "written by a capability"


def test_the_destructive_capability_removes_the_file(trusting_guard: Guard, workspace: Path) -> None:
    target = workspace / "note.txt"

    verdict = trusting_guard.evaluate(invoke("delete_file", path=str(target)))

    assert isinstance(verdict, Allowed)
    assert verdict.effective_side_effect == "destructive"
    verdict.capability.handler(**verdict.resolved_args)
    assert not target.exists()


def test_a_handler_receives_the_canonical_path_not_the_input(trusting_guard: Guard, workspace: Path) -> None:
    """The handler is handed the resolved value, so it cannot re-derive a different path from the
    string the caller wrote. This is why the handlers are three lines each."""
    (workspace / "sub").mkdir()
    messy = str(workspace / "sub" / ".." / "note.txt")

    verdict = trusting_guard.evaluate(invoke("read_text_file", path=messy))

    assert isinstance(verdict, Allowed)
    assert verdict.resolved_args["path"] == str((workspace / "note.txt").resolve())


# --- step 1: the name whitelist ---------------------------------------------------------------


def test_an_unregistered_capability_is_denied(guard: Guard) -> None:
    result = guard.evaluate(invoke("format_c_drive", path="C:/"))

    assert isinstance(result, Denied)
    assert result.step == "name_whitelist"


# --- step 2: the arguments, which is the step the name proves nothing about --------------------


def test_a_whitelisted_name_with_an_unknown_argument_is_denied(guard: Guard, workspace: Path) -> None:
    """extra="forbid": a smuggled field is a rejected invocation, not an ignored one."""
    result = guard.evaluate(invoke("read_text_file", path=str(workspace / "note.txt"), sudo=True))

    assert isinstance(result, Denied)
    assert result.step == "argument_schema"


def test_a_whitelisted_name_with_a_wrong_typed_argument_is_denied(guard: Guard) -> None:
    result = guard.evaluate(invoke("read_text_file", path=42))

    assert isinstance(result, Denied)
    assert result.step == "argument_schema"


def test_a_whitelisted_name_with_a_missing_argument_is_denied(guard: Guard) -> None:
    result = guard.evaluate(invoke("read_text_file"))

    assert isinstance(result, Denied)
    assert result.step == "argument_schema"


# --- step 3: containment ----------------------------------------------------------------------


def test_a_path_outside_the_allowed_root_is_denied(guard: Guard, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = guard.evaluate(invoke("read_text_file", path=str(outside)))

    assert isinstance(result, Denied)
    assert result.step == "path_containment"


def test_traversal_out_of_the_root_is_denied(guard: Guard, workspace: Path) -> None:
    result = guard.evaluate(invoke("read_text_file", path=str(workspace / ".." / "Windows")))

    assert isinstance(result, Denied)
    assert result.step == "path_containment"


def test_a_read_inside_the_workspace_is_allowed(guard: Guard, workspace: Path) -> None:
    """The one path through the whole chain that ends in execution."""
    result = guard.evaluate(invoke("read_text_file", path=str(workspace / "note.txt")))

    assert isinstance(result, Allowed)
    assert result.effective_side_effect == "read"
    assert result.affected_paths == ((workspace / "note.txt").resolve(),)


# --- step 4: approval routing -----------------------------------------------------------------


def test_a_write_is_queued_rather_than_executed(guard: Guard, workspace: Path) -> None:
    target = workspace / "new.txt"

    result = guard.evaluate(invoke("write_text_file", path=str(target), content="x"))

    assert isinstance(result, Pending)
    assert not target.exists(), "the handler must not have run"


def test_a_destructive_capability_cannot_execute_without_approval(guard: Guard, workspace: Path) -> None:
    """ROADMAP M2 names this one explicitly, and M3 keeps it true by queueing rather than denying.

    The distinction that matters is unchanged: nothing ran. Whether it is refused outright or waiting
    on a human, the file is still there.
    """
    target = workspace / "note.txt"

    result = guard.evaluate(invoke("delete_file", path=str(target)))

    assert isinstance(result, Pending)
    assert result.side_effect == "destructive"
    assert target.exists(), "the file must still be there"


def test_a_queued_request_carries_the_resolved_arguments(guard: Guard, workspace: Path) -> None:
    """What reaches the dialog is what will run, not what was asked for."""
    (workspace / "sub").mkdir()
    messy = str(workspace / "sub" / ".." / "note.txt")

    result = guard.evaluate(invoke("delete_file", path=messy))

    assert isinstance(result, Pending)
    assert result.resolved_args["path"] == str((workspace / "note.txt").resolve())
    assert result.affected_paths == ((workspace / "note.txt").resolve(),)


def test_an_invocation_already_rejected_is_not_queued_again(guard: Guard, workspace: Path) -> None:
    """SECURITY.md section 5: a rejected operation is not retried in the same session.

    Without this, 'no' means 'ask again' - and something that can ask twice can ask until the human
    clicks the wrong button.
    """
    invocation = invoke("delete_file", path=str(workspace / "note.txt"))
    first = guard.evaluate(invocation)
    assert isinstance(first, Pending)

    guard.record_rejection(first)

    second = guard.evaluate(invocation)

    assert isinstance(second, Denied)
    assert second.step == "already_rejected"
    assert second.decision == "denied_user"


def test_a_read_outside_the_workspace_is_escalated_to_destructive(tmp_path: Path) -> None:
    """Decision 1 of the M2 plan: allowed_roots may be wide, and width costs an approval rather than
    costing containment. A capability declaring a whole drive still cannot quietly read outside the
    workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "note.txt").write_text("x", encoding="utf-8")

    guard = Guard(
        registry=build_registry(workspace, wide_root=tmp_path),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
    )

    result = guard.evaluate(invoke("read_text_file", path=str(elsewhere / "note.txt")))

    assert isinstance(result, Pending)
    assert result.side_effect == "destructive", "declared read, escalated for leaving the workspace"


# --- step 5: origin ---------------------------------------------------------------------------


def test_an_untrusted_origin_write_is_queued_carrying_its_origin(guard: Guard, workspace: Path) -> None:
    """SECURITY.md section 6: such an invocation *surfaces for approval* with the untrusted
    treatment, or is denied. It is not denied outright.

    So origin's job here is to reach the dialog intact - the UI is what makes it visually distinct
    and defaults the selection to Reject. Nothing passes automatically while trust is off, which is
    why origin no longer needs to deny anything by itself.
    """
    target = workspace / "x.txt"

    result = guard.evaluate(
        invoke("write_text_file", origin="untrusted_content", path=str(target), content="x")
    )

    assert isinstance(result, Pending)
    assert result.origin == "untrusted_content"
    assert not target.exists()


def test_untrusted_origin_may_still_read(guard: Guard, workspace: Path) -> None:
    """The origin rule bites on write and destructive. A read inside the workspace is not what
    section 6 is defending against."""
    result = guard.evaluate(
        invoke("read_text_file", origin="untrusted_content", path=str(workspace / "note.txt"))
    )

    assert isinstance(result, Allowed)


# --- trust mode, which is the button ------------------------------------------------------------


def test_trust_mode_lets_a_destructive_operation_through_without_a_dialog(
    trusting_guard: Guard, workspace: Path
) -> None:
    """The button, doing what the user asked for."""
    result = trusting_guard.evaluate(invoke("delete_file", path=str(workspace / "note.txt")))

    assert isinstance(result, Allowed)
    assert result.auto_approved is True


def test_trust_mode_lets_untrusted_content_through_too(trusting_guard: Guard, workspace: Path) -> None:
    """The consequence the user accepted explicitly, asserted rather than left implicit.

    With the button on there are no exceptions: an operation that exists because of content Local
    Zero merely read executes with no human in the loop. This test exists so that the day someone
    wants to narrow the button, the thing they are changing is visible and named.
    """
    result = trusting_guard.evaluate(
        invoke("write_text_file", origin="untrusted_content", path=str(workspace / "x.txt"), content="x")
    )

    assert isinstance(result, Allowed)
    assert result.auto_approved is True


def test_trust_mode_does_not_bypass_containment(trusting_guard: Guard, tmp_path: Path) -> None:
    """The property that keeps the button the user's.

    Trust mode skips the approval gate, not the guard. Steps 1-3 run in every mode, which is why the
    trust file itself - sitting outside every allowed_root - cannot be reached by a capability even
    when approval is switched off.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = trusting_guard.evaluate(invoke("read_text_file", path=str(outside)))

    assert isinstance(result, Denied)
    assert result.step == "path_containment"


def test_trust_mode_does_not_bypass_argument_validation(trusting_guard: Guard, workspace: Path) -> None:
    result = trusting_guard.evaluate(
        invoke("read_text_file", path=str(workspace / "note.txt"), sudo=True)
    )

    assert isinstance(result, Denied)
    assert result.step == "argument_schema"


def test_a_protected_control_file_is_refused_even_with_a_root_that_covers_it(tmp_path: Path) -> None:
    """No capability writes Local Zero's own controls, whatever its allowed_roots says.

    Red line 8's sibling. This is what keeps the trust file out of reach in M5, when capabilities
    start declaring roots wide enough to contain it.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trust_file = tmp_path / "trust.json"
    trust_file.write_text("{}", encoding="utf-8")

    guard = Guard(
        registry=build_registry(workspace, wide_root=tmp_path),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        protected_paths=(trust_file,),
    )

    result = guard.evaluate(invoke("read_text_file", path=str(trust_file)))

    assert isinstance(result, Denied)
    assert result.step == "protected_path"


def test_untrusted_origin_may_still_read(guard: Guard, workspace: Path) -> None:
    """The origin rule bites on write and destructive. A read inside the workspace is not what
    section 6 is defending against."""
    result = guard.evaluate(
        invoke("read_text_file", origin="untrusted_content", path=str(workspace / "note.txt"))
    )

    assert isinstance(result, Allowed)


# --- ordering ---------------------------------------------------------------------------------


def test_the_chain_stops_at_the_first_failing_step(guard: Guard) -> None:
    """An unregistered name with a hostile path must report the name step, not the path step. If a
    later step can report first, the order in SECURITY.md is decorative."""
    result = guard.evaluate(invoke("not_a_capability", path="C:/Windows/System32/config/SAM"))

    assert isinstance(result, Denied)
    assert result.step == "name_whitelist"


# --- the audit trail --------------------------------------------------------------------------


def audit_entries(tmp_path: Path) -> list[dict]:
    log = tmp_path / "logs" / "audit.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def test_every_decision_is_audited_including_denials(guard: Guard, tmp_path: Path, workspace: Path) -> None:
    guard.evaluate(invoke("read_text_file", path=str(workspace / "note.txt")))
    guard.evaluate(invoke("read_text_file", path="C:/Windows/System32/config/SAM"))
    guard.evaluate(invoke("nope", path="x"))

    entries = audit_entries(tmp_path)
    assert [entry["decision"] for entry in entries] == ["allowed", "denied_guard", "denied_guard"]


def test_the_audit_records_which_step_denied(guard: Guard, tmp_path: Path) -> None:
    guard.evaluate(invoke("read_text_file", path="C:/Windows/System32/config/SAM"))

    assert audit_entries(tmp_path)[0]["reason"].startswith("path_containment")


def test_the_audit_never_contains_the_raw_arguments(guard: Guard, tmp_path: Path) -> None:
    guard.evaluate(invoke("read_text_file", path="C:/Users/someone/tax-return-2025.pdf"))

    raw = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert "tax-return-2025" not in raw
