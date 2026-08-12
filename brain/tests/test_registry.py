"""Registration: a capability does not exist unless it is declared completely.

docs/SECURITY.md section 4 lists five fields and treats all five as mandatory. The point of testing
registration separately from the guard is that a half-declared capability must fail at the moment it
is registered - at import, where a human sees it - rather than at the moment it is invoked, where the
guard would have to decide what a missing ``allowed_roots`` means.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from local_zero_brain.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    PathArgument,
)


class SampleArgs(BaseModel):
    path: PathArgument


def handler(_: SampleArgs) -> str:
    return "ran"


def make(name: str = "read_text_file", side_effect: str = "read") -> Capability:
    return Capability(
        name=name,
        args_schema=SampleArgs,
        side_effect=side_effect,
        allowed_roots=(Path("C:/nowhere"),),
        handler=handler,
    )


def test_a_registered_capability_is_found_by_name() -> None:
    registry = CapabilityRegistry([make()])

    assert registry.get("read_text_file") is not None


def test_an_unregistered_name_is_not_found() -> None:
    """Step 1 of the guard chain depends on this returning nothing rather than improvising."""
    registry = CapabilityRegistry([make()])

    assert registry.get("delete_everything") is None


def test_the_registry_is_not_mutable_after_construction() -> None:
    """A registry that can be added to at runtime is a registry an attacker only has to reach once.
    The Reader in M4 is constructed with an empty one and must stay empty."""
    registry = CapabilityRegistry([make()])

    # TypeError rather than AttributeError: the mapping is exposed as a mappingproxy, which refuses
    # item assignment outright rather than lacking the method.
    with pytest.raises(TypeError):
        registry.capabilities["evil"] = make("evil")  # type: ignore[index]


def test_an_empty_registry_is_legal_and_finds_nothing() -> None:
    """SECURITY.md invariant 4: the Reader is constructed with an empty capability registry, and a
    test asserts its length is zero. This is that assertion's home."""
    registry = CapabilityRegistry([])

    assert len(registry) == 0
    assert registry.get("read_text_file") is None


def test_a_duplicate_name_is_refused() -> None:
    """Two capabilities with one name means the whitelist answer depends on ordering."""
    with pytest.raises(ValueError, match="duplicate"):
        CapabilityRegistry([make(), make()])


@pytest.mark.parametrize("side_effect", ["read", "write", "destructive"])
def test_the_three_side_effects_are_accepted(side_effect: str) -> None:
    assert make(side_effect=side_effect).side_effect == side_effect


def test_an_unknown_side_effect_is_refused() -> None:
    """The guard routes on this value. A fourth one would route nowhere, and 'routes nowhere' must
    not be reachable by declaring it."""
    with pytest.raises(ValueError, match="side_effect"):
        make(side_effect="harmless")


def test_a_capability_with_no_allowed_roots_is_refused() -> None:
    """An empty tuple would make containment vacuously false, which is safe, or vacuously true,
    which is catastrophic. Neither should be expressible."""
    with pytest.raises(ValueError, match="allowed_roots"):
        Capability(
            name="read_text_file",
            args_schema=SampleArgs,
            side_effect="read",
            allowed_roots=(),
            handler=handler,
        )


def test_allowed_roots_must_be_absolute() -> None:
    """A relative root resolves against the working directory, so what a capability is allowed to
    touch would depend on where the process happened to be started."""
    with pytest.raises(ValueError, match="absolute"):
        Capability(
            name="read_text_file",
            args_schema=SampleArgs,
            side_effect="read",
            allowed_roots=(Path("relative/root"),),
            handler=handler,
        )


def test_a_capability_is_frozen() -> None:
    """Nothing rewrites a capability's side_effect after registration, least of all the thing whose
    invocation is being judged by it."""
    capability = make()

    with pytest.raises(Exception):
        capability.side_effect = "read"  # type: ignore[misc]


class PathlessArgs(BaseModel):
    """A capability that takes no path at all. ``list_processes`` is the real one."""

    limit: int = 50


class TestAllowedRootsMeanWhatTheySay:
    """``allowed_roots`` is the containment step's whole input, so it has to describe something.

    The rule used to be "at least one root, always". That reads as strict and is not: a capability
    with no path argument has nothing for the guard to contain, so ``path_fields`` is empty,
    ``affected_paths`` is empty, and whatever roots were declared are never consulted. Handing such
    a capability the workspace to satisfy the constructor writes a containment claim that no code
    enforces - and a security control that is decoration is worse than an absent one, because a
    reader counts it.

    So the requirement is now conditional, in both directions.
    """

    def test_a_capability_with_a_path_still_needs_roots(self) -> None:
        """The original rule, unchanged where it was doing work."""
        with pytest.raises(ValueError, match="allowed_roots"):
            Capability(
                name="read_text_file",
                args_schema=SampleArgs,
                side_effect="read",
                allowed_roots=(),
                handler=handler,
            )

    def test_a_capability_with_no_path_may_declare_no_roots(self) -> None:
        capability = Capability(
            name="list_processes",
            args_schema=PathlessArgs,
            side_effect="read",
            allowed_roots=(),
            handler=handler,
        )

        assert capability.allowed_roots == ()

    def test_a_capability_with_no_path_may_not_declare_roots_either(self) -> None:
        """The half that makes this a rule rather than a relaxation.

        Roots on a capability the guard will never check are a claim about containment that nothing
        enforces. Refusing them at registration is what keeps the field readable as fact.
        """
        with pytest.raises(ValueError, match="no path arguments"):
            Capability(
                name="list_processes",
                args_schema=PathlessArgs,
                side_effect="read",
                allowed_roots=(Path("C:/nowhere"),),
                handler=handler,
            )

    def test_the_absolute_rule_still_applies_to_a_capability_that_has_paths(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            Capability(
                name="read_text_file",
                args_schema=SampleArgs,
                side_effect="read",
                allowed_roots=(Path("relative/root"),),
                handler=handler,
            )
