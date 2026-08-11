"""What a capability is, and the set of them that exists.

docs/SECURITY.md section 4: *a capability does not exist unless it is registered with all five
fields.* The five are enforced here rather than assumed, and they are checked at construction so an
incomplete declaration fails where a human is looking - at import - instead of at the moment
something is trying to invoke it.

The registry is deliberately immutable and deliberately boring. Invariant 4 of SECURITY.md section 2
says the Reader in M4 is constructed with an *empty* registry and a test asserts its length is zero;
that only means anything if a registry cannot acquire capabilities after it is built.

Note the asymmetry that decision 1 of the M2 plan rests on: ``allowed_roots`` may be as wide as a
whole drive, because "Local Zero can act on this machine" is the point of the product. Width is not
the control. The control is that the guard escalates anything resolving outside the workspace to
``destructive``, so breadth costs an approval rather than costing containment.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

#: read may execute directly; write and destructive route to approval. The guard switches on this,
#: so a value outside the three would route nowhere - which is why it is a closed set.
SideEffect = Literal["read", "write", "destructive"]

SIDE_EFFECTS: tuple[str, ...] = get_args(SideEffect)

#: A path as it arrives from a caller: a non-empty string and nothing more.
#:
#: It is deliberately *not* a ``Path`` here. Parsing it into one would imply the value has been
#: understood, and it has not - it is an untrusted string until paths.resolve_within has canonicalised
#: it and proven where it lands. Schema validation is step 2; containment is step 3; they do not get
#: to swap places.
#:
#: The marker is what makes step 3 find these fields. Guessing by name - "path", "file", "dst" -
#: would mean a capability could put a path somewhere the guard does not look simply by calling the
#: argument something else, and the omission would be silent.
PATH_MARKER = "lz_path"

PathArgument = Annotated[str, Field(min_length=1, json_schema_extra={PATH_MARKER: True})]


class CapabilityArgs(BaseModel):
    """Base for every capability's ``args_schema``: an unknown field is a rejected invocation.

    Separate from contracts.common.ContractModel on purpose. That one mirrors the wire schemas and
    is versioned with them; this one governs arguments that never appear on a wire at all.
    """

    model_config = ConfigDict(extra="forbid")


def path_fields(schema: type[BaseModel]) -> tuple[str, ...]:
    """The names of the path-typed arguments, in declaration order."""
    found = []

    for name, info in schema.model_fields.items():
        extra = info.json_schema_extra
        if isinstance(extra, dict) and extra.get(PATH_MARKER):
            found.append(name)

    return tuple(found)


@dataclass(frozen=True, slots=True)
class Capability:
    """One registered operation, complete in all five fields."""

    #: The stable identifier. The whitelist matches on this and only this - which is why matching on
    #: it proves nothing on its own, and why step 2 exists.
    name: str
    #: The real validation: types, ranges, enums, patterns, and extra="forbid" via ContractModel.
    args_schema: type[BaseModel]
    side_effect: SideEffect
    #: Absolute directory roots outside which any path argument is refused.
    allowed_roots: tuple[Path, ...]
    handler: Callable[..., object]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a capability must have a name")

        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(
                f"side_effect must be one of {SIDE_EFFECTS}, not {self.side_effect!r}: "
                "the guard routes on this value and an unknown one routes nowhere"
            )

        if not self.allowed_roots:
            raise ValueError(
                "a capability must declare at least one entry in allowed_roots. An empty tuple "
                "makes containment vacuous, and the two ways of being vacuous are 'refuses "
                "everything' and 'permits everything'"
            )

        for root in self.allowed_roots:
            if not root.is_absolute():
                raise ValueError(
                    f"allowed_roots must be absolute; {root!r} is relative, which would make what "
                    "this capability may touch depend on the process's working directory"
                )


class CapabilityRegistry:
    """The set of capabilities that exist. Fixed once constructed."""

    __slots__ = ("_capabilities",)

    def __init__(self, capabilities: Iterable[Capability]) -> None:
        registered: dict[str, Capability] = {}

        for capability in capabilities:
            if capability.name in registered:
                raise ValueError(
                    f"duplicate capability name {capability.name!r}: with two registrations under "
                    "one name, what the whitelist answers depends on iteration order"
                )
            registered[capability.name] = capability

        # A read-only view, so nothing acquires a capability after construction. The Reader path in
        # M4 depends on an empty registry staying empty.
        self._capabilities: Mapping[str, Capability] = MappingProxyType(registered)

    @property
    def capabilities(self) -> Mapping[str, Capability]:
        return self._capabilities

    def get(self, name: str) -> Capability | None:
        """The whitelist lookup. Returns None rather than raising: an unregistered name is an
        ordinary denial, not an exceptional condition."""
        return self._capabilities.get(name)

    def __len__(self) -> int:
        return len(self._capabilities)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._capabilities.values())
