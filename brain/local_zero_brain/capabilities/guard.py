"""The guard chain: five steps, fixed order, every one of them failing closed.

docs/SECURITY.md section 4 fixes the order and the reason for it. Restated because the ordering is
the design:

1. **name whitelist** - bounds the surface and proves nothing else
2. **argument schema** - the real validation. A whitelist that only checks the name is not a control:
   knowing ``read_text_file`` is registered says nothing about whether
   ``..\\..\\Windows\\System32\\config\\SAM`` is an acceptable argument to it
3. **path canonicalisation and containment** - on the resolved path, never the input string
4. **approval routing** - anything that is not a read stops here
5. **origin** - untrusted content never auto-passes a write or a destructive, whatever step 4 decided

Any step raising is a denial rather than a fallthrough. That is what "fails closed" means here, and
it is why every step is wrapped rather than trusted to behave.

**The escalation rule** (M2 plan decision 1) sits between steps 3 and 4. ``allowed_roots`` may be as
wide as a drive, because acting on this machine is the point of the product. What stops width from
dissolving containment is that a path resolving outside the workspace is treated as ``destructive``
whatever its capability declared - so breadth costs an approval instead of costing the boundary.

**There is no approver until M3.** ``DenyingApprover`` is the only implementation, and it denies with
a stated reason. "A destructive capability cannot execute without approval" is therefore true in the
strongest available sense: approving is not yet an action anything can take.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from local_zero_brain.approvals import ApprovalQueue, RejectionMemory
from local_zero_brain.audit import AuditLog, AuditRecord, Decision, Origin, hash_arguments
from local_zero_brain.capabilities.paths import Contained, Refused, resolve_within
from local_zero_brain.capabilities.registry import Capability, CapabilityRegistry, SideEffect, path_fields
from local_zero_brain.trust import TrustStore


@dataclass(frozen=True, slots=True)
class Invocation:
    """A proposed operation. In M2 these are hardcoded by tests; in M4 a planner proposes them.

    ``origin`` is assigned by the brain at the point the request enters, and is never derived from
    anything a model says about itself - see SECURITY.md section 6.
    """

    capability: str
    args: dict
    origin: Origin = "user_direct"


@dataclass(frozen=True, slots=True)
class Allowed:
    """Passed every step. ``resolved_args`` is post-validation and post-canonicalisation."""

    capability: Capability
    resolved_args: dict
    affected_paths: tuple[Path, ...]
    effective_side_effect: SideEffect
    #: True when trust mode let this through without a human seeing it. Carried so the audit can
    #: record it and the UI can show what the button allowed, rather than it being invisible.
    auto_approved: bool = False


@dataclass(frozen=True, slots=True)
class Pending:
    """Needs a human. The guard decides; it does not wait.

    Making ``evaluate`` async so it could block on an answer would push async through every caller
    and every test of a function whose job is to judge an invocation. Instead the caller takes this,
    raises the dialog, and executes or does not.
    """

    request_id: str
    capability: Capability
    resolved_args: dict
    affected_paths: tuple[Path, ...]
    side_effect: SideEffect
    origin: Origin


@dataclass(frozen=True, slots=True)
class Denied:
    """Refused, naming the step so the audit can say which control caught it."""

    step: str
    decision: Decision
    reason: str
    #: Set when the escalation rule changed the side effect out from under the declaration.
    escalated_side_effect: SideEffect | None = None


Verdict = Allowed | Denied | Pending


class Guard:
    """Evaluates an invocation against the chain and records the outcome, always."""

    __slots__ = ("_registry", "_workspace", "_audit", "_trust", "_queue", "_rejections", "_protected")

    def __init__(
        self,
        registry: CapabilityRegistry,
        workspace: Path,
        audit: AuditLog,
        trust: TrustStore | None = None,
        queue: ApprovalQueue | None = None,
        rejections: RejectionMemory | None = None,
        protected_paths: Sequence[Path] = (),
    ) -> None:
        self._registry = registry
        self._workspace = workspace.resolve(strict=False)
        self._audit = audit
        #: No trust store means the button has never been pressed, which reads as off. Absence is
        #: never permission.
        self._trust = trust
        self._queue = queue or ApprovalQueue()
        self._rejections = rejections or RejectionMemory()
        #: Local Zero's own control files. Refused whatever a capability's allowed_roots says - red
        #: line 8's sibling, and what keeps the trust file unreachable when M5 widens those roots.
        self._protected = tuple(path.resolve(strict=False) for path in protected_paths)

    @property
    def queue(self) -> ApprovalQueue:
        return self._queue

    def audit_decision(self, pending: Pending, *, decision: Decision, reason: str) -> None:
        """Records how a queued request was finally settled.

        The queued entry was written when the request was raised; this is the second half of it. For
        an approved invocation it is written *before* the handler runs, so a crash mid-operation
        still leaves a record - docs/SECURITY.md section 9.
        """
        self._audit.record(
            AuditRecord(
                origin=pending.origin,
                capability=pending.capability.name,
                resolved_args=pending.resolved_args,
                affected_paths=[str(path) for path in pending.affected_paths],
                side_effect=pending.side_effect,
                decision=decision,
                reason=reason,
            )
        )

    def record_rejection(self, pending: Pending) -> None:
        """Remembers that the user said no, so the identical invocation is not queued again.

        Keyed on the capability's *name*, matching what the check in the chain uses. Keying on the
        Capability object instead still hashes - it is a frozen dataclass - so the mismatch is silent
        and the memory simply never matches anything. A test caught it; nothing else would have.
        """
        self._rejections.remember(pending.capability.name, hash_arguments(pending.resolved_args))

    def evaluate(self, invocation: Invocation) -> Verdict:
        verdict = self._run_chain(invocation)
        self._audit_verdict(invocation, verdict)
        return verdict

    # --- the chain ----------------------------------------------------------------------------

    def _run_chain(self, invocation: Invocation) -> Verdict:
        capability = self._registry.get(invocation.capability)
        if capability is None:
            return Denied(
                "name_whitelist",
                "denied_guard",
                "the capability is not registered. This step bounds the surface; it does not "
                "establish that anything is safe",
            )

        try:
            validated = capability.args_schema(**invocation.args)
        except (ValidationError, TypeError) as error:
            return Denied("argument_schema", "denied_guard", _summarize(error))

        resolved_args = validated.model_dump()

        containment = self._contain_paths(capability, resolved_args)
        if isinstance(containment, Denied):
            return containment

        affected_paths, resolved_args = containment

        protected = self._refuse_protected(affected_paths)
        if protected is not None:
            return protected

        effective = self._escalate(capability.side_effect, affected_paths)

        # The button. Chosen by the user with the consequence stated: approval is bypassed for every
        # invocation regardless of side_effect or origin. One branch, in one place, so what it skips
        # is readable rather than scattered - and note what is already behind it. Steps 1-3 ran
        # above, so trust mode is skipping the gate, not the guard.
        if self._trust is not None and self._trust.load().enabled:
            return Allowed(
                capability=capability,
                resolved_args=resolved_args,
                affected_paths=affected_paths,
                effective_side_effect=effective,
                auto_approved=True,
            )

        if effective == "read":
            return Allowed(
                capability=capability,
                resolved_args=resolved_args,
                affected_paths=affected_paths,
                effective_side_effect=effective,
            )

        # SECURITY.md section 5: a rejected operation is not re-attempted in the same session.
        # Checked before queueing, so a refused invocation cannot come back as a second dialog.
        if self._rejections.was_rejected(capability.name, hash_arguments(resolved_args)):
            return Denied(
                "already_rejected",
                "denied_user",
                "this exact invocation was refused earlier in this session and is not re-offered",
            )

        # Nothing passes automatically while the button is off, so origin no longer needs to deny by
        # itself - SECURITY.md section 6 says an untrusted_content invocation surfaces for approval
        # with the untrusted treatment. It is carried into the payload, and the dialog is what makes
        # it visually distinct and defaults the selection to Reject.
        pending = self._queue.open(
            capability=capability.name,
            resolved_args=resolved_args,
            affected_paths=[str(path) for path in affected_paths],
            side_effect=effective,
            origin=invocation.origin,
        )

        return Pending(
            request_id=pending.request_id,
            capability=capability,
            resolved_args=resolved_args,
            affected_paths=affected_paths,
            side_effect=effective,
            origin=invocation.origin,
        )

    def _refuse_protected(self, affected_paths: tuple[Path, ...]) -> Denied | None:
        """Local Zero's own control files are out of reach for every capability.

        Containment already keeps them out while roots stay narrow. This holds the line when a
        capability legitimately declares a root wide enough to contain one - which is what M5 is
        for. A capability that could write the trust file could turn approval off, and then approval
        would not be there to stop it.
        """
        for path in affected_paths:
            if any(path == protected for protected in self._protected):
                return Denied(
                    "protected_path",
                    "denied_guard",
                    "the path is one of Local Zero's own control files, which no capability may "
                    "touch whatever its allowed_roots permits",
                )

        return None

    def _contain_paths(
        self, capability: Capability, resolved_args: dict
    ) -> tuple[tuple[Path, ...], dict] | Denied:
        """Step 3. Every path-typed argument is resolved and proven contained, and the canonical
        form replaces the input in ``resolved_args`` - nothing downstream sees the raw string."""
        affected: list[Path] = []
        canonical = dict(resolved_args)

        for name in path_fields(capability.args_schema):
            raw = resolved_args.get(name)
            if not isinstance(raw, str):
                return Denied("path_containment", "denied_guard", f"{name}: not a path-shaped value")

            outcome = resolve_within(raw, capability.allowed_roots)
            # Positive check rather than `if isinstance(outcome, Refused): ...` followed by an
            # assert. `python -O` strips asserts, so an assert on a guard path is one refactor away
            # from being the thing that was holding the door shut. Anything that is not positively
            # Contained is a denial.
            if not isinstance(outcome, Contained):
                reason = outcome.reason if isinstance(outcome, Refused) else "containment returned no verdict"
                return Denied("path_containment", "denied_guard", f"{name}: {reason}")

            canonical[name] = str(outcome.path)
            affected.append(outcome.path)

        return tuple(affected), canonical

    def _escalate(self, declared: SideEffect, affected_paths: tuple[Path, ...]) -> SideEffect:
        """Anything touching outside the workspace is destructive, whatever it called itself."""
        for path in affected_paths:
            if not _is_inside(path, self._workspace):
                return "destructive"

        return declared

    # --- the record ---------------------------------------------------------------------------

    def _audit_verdict(self, invocation: Invocation, verdict: Verdict) -> None:
        if isinstance(verdict, Allowed):
            self._audit.record(
                AuditRecord(
                    origin=invocation.origin,
                    capability=invocation.capability,
                    resolved_args=verdict.resolved_args,
                    affected_paths=[str(path) for path in verdict.affected_paths],
                    side_effect=verdict.effective_side_effect,
                    decision="allowed",
                    reason="trust mode was on; no human saw this" if verdict.auto_approved else "",
                    trust_mode=verdict.auto_approved,
                )
            )
            return

        if isinstance(verdict, Pending):
            # Recorded when raised rather than only when answered, so a request that is never
            # answered still leaves a trace. The outcome is recorded separately by whoever resolves
            # it.
            self._audit.record(
                AuditRecord(
                    origin=invocation.origin,
                    capability=invocation.capability,
                    resolved_args=verdict.resolved_args,
                    affected_paths=[str(path) for path in verdict.affected_paths],
                    side_effect=verdict.side_effect,
                    decision="queued",
                    reason=f"awaiting approval, request {verdict.request_id}",
                )
            )
            return

        # A denied invocation's arguments were never canonicalised, so the raw ones are hashed. The
        # hash is all that is written either way.
        self._audit.record(
            AuditRecord(
                origin=invocation.origin,
                capability=invocation.capability,
                resolved_args=invocation.args,
                affected_paths=[],
                side_effect=verdict.escalated_side_effect or "unknown",
                decision=verdict.decision,
                reason=f"{verdict.step}: {verdict.reason}",
            )
        )


def _is_inside(path: Path, root: Path) -> bool:
    import os

    return Path(os.path.normcase(str(path))).is_relative_to(Path(os.path.normcase(str(root))))


def _summarize(error: Exception) -> str:
    """Names the offending field without echoing its value - the same rule as ipc/session.py."""
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return f"{location}: {first['msg']}"

    return "the arguments do not match the declared schema"
