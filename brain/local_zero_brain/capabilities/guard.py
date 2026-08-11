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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from local_zero_brain.audit import AuditLog, AuditRecord, Decision, Origin
from local_zero_brain.capabilities.paths import Contained, Refused, resolve_within
from local_zero_brain.capabilities.registry import Capability, CapabilityRegistry, SideEffect, path_fields


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


@dataclass(frozen=True, slots=True)
class Denied:
    """Refused, naming the step so the audit can say which control caught it."""

    step: str
    decision: Decision
    reason: str
    #: Set when the escalation rule changed the side effect out from under the declaration.
    escalated_side_effect: SideEffect | None = None


Verdict = Allowed | Denied


class Approver(Protocol):
    """The seam M3 fills. Returns a reason to deny, or None to allow."""

    def deny_reason(self, capability: Capability, affected_paths: tuple[Path, ...]) -> str | None: ...


class DenyingApprover:
    """M2's only approver: everything that needs approval is denied, because approving does not
    exist yet. Replaced in M3 by the real flow, not extended here."""

    def deny_reason(self, capability: Capability, affected_paths: tuple[Path, ...]) -> str | None:
        return (
            "this operation requires approval, and no approval flow exists before M3. "
            "It is refused rather than queued"
        )


class Guard:
    """Evaluates an invocation against the chain and records the outcome, always."""

    __slots__ = ("_registry", "_workspace", "_audit", "_approver")

    def __init__(
        self,
        registry: CapabilityRegistry,
        workspace: Path,
        audit: AuditLog,
        approver: Approver | None = None,
    ) -> None:
        self._registry = registry
        self._workspace = workspace.resolve(strict=False)
        self._audit = audit
        self._approver = approver or DenyingApprover()

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

        effective = self._escalate(capability.side_effect, affected_paths)

        if effective != "read":
            reason = self._approver.deny_reason(capability, affected_paths)
            if reason is not None:
                return Denied(
                    "approval",
                    "denied_guard",
                    reason,
                    escalated_side_effect=effective if effective != capability.side_effect else None,
                )

        # Step 5 runs last and can still deny, whatever step 4 concluded. SECURITY.md section 4:
        # untrusted content never auto-passes a write or a destructive, ever.
        if invocation.origin == "untrusted_content" and effective != "read":
            return Denied(
                "origin",
                "denied_origin",
                "the request exists because of untrusted content, which may never perform a write "
                "or a destructive operation automatically",
            )

        return Allowed(
            capability=capability,
            resolved_args=resolved_args,
            affected_paths=affected_paths,
            effective_side_effect=effective,
        )

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
                    reason="",
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
