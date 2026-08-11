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
)
from local_zero_brain.capabilities.handlers import build_registry


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


class AllowingApprover:
    """Stands in for the M3 approval flow.

    Used only where a test needs the chain to get past step 4, because M2's real approver denies
    everything and would otherwise leave step 5 and the handlers unreachable.
    """

    def deny_reason(self, capability: object, affected_paths: tuple[Path, ...]) -> str | None:
        return None


@pytest.fixture
def approved_guard(workspace: Path, tmp_path: Path) -> Guard:
    return Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        approver=AllowingApprover(),
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


def test_the_write_capability_creates_the_file(approved_guard: Guard, workspace: Path) -> None:
    target = workspace / "written.txt"

    verdict = approved_guard.evaluate(
        invoke("write_text_file", path=str(target), content="written by a capability")
    )

    assert isinstance(verdict, Allowed)
    verdict.capability.handler(**verdict.resolved_args)
    assert target.read_text(encoding="utf-8") == "written by a capability"


def test_the_destructive_capability_removes_the_file(approved_guard: Guard, workspace: Path) -> None:
    target = workspace / "note.txt"

    verdict = approved_guard.evaluate(invoke("delete_file", path=str(target)))

    assert isinstance(verdict, Allowed)
    assert verdict.effective_side_effect == "destructive"
    verdict.capability.handler(**verdict.resolved_args)
    assert not target.exists()


def test_a_handler_receives_the_canonical_path_not_the_input(approved_guard: Guard, workspace: Path) -> None:
    """The handler is handed the resolved value, so it cannot re-derive a different path from the
    string the caller wrote. This is why the handlers are three lines each."""
    (workspace / "sub").mkdir()
    messy = str(workspace / "sub" / ".." / "note.txt")

    verdict = approved_guard.evaluate(invoke("read_text_file", path=messy))

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


def test_a_write_does_not_execute_without_approval(guard: Guard, workspace: Path) -> None:
    target = workspace / "new.txt"

    result = guard.evaluate(invoke("write_text_file", path=str(target), content="x"))

    assert isinstance(result, Denied)
    assert result.step == "approval"
    assert not target.exists(), "the handler must not have run"


def test_a_destructive_capability_cannot_execute_without_approval(guard: Guard, workspace: Path) -> None:
    """ROADMAP M2 names this one explicitly."""
    target = workspace / "note.txt"

    result = guard.evaluate(invoke("delete_file", path=str(target)))

    assert isinstance(result, Denied)
    assert result.step == "approval"
    assert target.exists(), "the file must still be there"


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

    assert isinstance(result, Denied)
    assert result.step == "approval"
    assert result.escalated_side_effect == "destructive"


# --- step 5: origin ---------------------------------------------------------------------------


def test_untrusted_origin_cannot_write_even_inside_the_workspace(guard: Guard, workspace: Path) -> None:
    """Denied - but by step 4, not step 5, and that is correct rather than incidental.

    SECURITY.md section 6 says an untrusted_content write *surfaces for approval* with the untrusted
    treatment, or is denied. Origin does not pre-empt the queue; it stops automatic passage. With
    M2's approver denying everything, approval is simply the first step that says no.
    """
    target = workspace / "x.txt"

    result = guard.evaluate(
        invoke("write_text_file", origin="untrusted_content", path=str(target), content="x")
    )

    assert isinstance(result, Denied)
    assert result.step == "approval"
    assert not target.exists()


def test_origin_denies_a_write_that_approval_would_have_allowed(workspace: Path, tmp_path: Path) -> None:
    """The only test that reaches step 5 at all.

    Because M2's approver denies everything, the origin check is unreachable through the normal
    configuration - it would sit there untested until M3 made approval succeed, which is exactly when
    a hole in it would start to matter. So approval is stubbed permissive here purely to get the
    chain that far.
    """

    guard = Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        approver=AllowingApprover(),
    )
    target = workspace / "x.txt"

    result = guard.evaluate(
        invoke("write_text_file", origin="untrusted_content", path=str(target), content="x")
    )

    assert isinstance(result, Denied)
    assert result.step == "origin"
    assert result.decision == "denied_origin"
    assert not target.exists()


def test_a_trusted_write_passes_once_approval_allows_it(workspace: Path, tmp_path: Path) -> None:
    """The control for the test above: same permissive approver, trusted origin, and the chain
    completes. Without this, 'origin denied it' would be indistinguishable from 'the stub was
    broken'."""

    guard = Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        approver=AllowingApprover(),
    )

    result = guard.evaluate(
        invoke("write_text_file", origin="user_direct", path=str(workspace / "x.txt"), content="x")
    )

    assert isinstance(result, Allowed)
    assert result.effective_side_effect == "write"


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
