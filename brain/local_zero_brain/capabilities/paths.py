"""Resolving a path argument and proving it is inside an allowed root.

This is step 3 of the guard chain in docs/SECURITY.md section 4, and it is the step that document
calls the most common way a guard like this fails. The failure is always the same shape: the input
*string* looks contained while the *filesystem* disagrees.

Measured on the target machine, 2026-08-12, with a real junction on disk:

    raw input        <root>\\escape\\secret.txt      (escape is a junction to <outside>)
    resolve()        <outside>\\secret.txt
    naive prefix     contained = True     <- accepts the escape
    resolved check   contained = False

So: resolve first, compare second, and compare on path components rather than on characters.
``C:\\Allowed`` is a string prefix of ``C:\\AllowedEvil`` and neither ``startswith`` nor a bare
string comparison can tell those apart.

Nothing here touches the filesystem for the forms that are refused outright - a UNC path, a device
name or an extended-length prefix is rejected on inspection, before resolution gets a chance to
normalise it into something that looks reasonable.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

#: Names that address a device rather than a file, at any position in a path and with any extension.
#: ``NUL.txt`` is still NUL.
RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

#: ``\\?\`` tells Win32 to skip path normalisation, and ``\\.\`` addresses the device namespace.
#: Normalisation is the thing containment depends on, so neither is negotiable away.
BYPASS_PREFIXES = ("\\\\?\\", "\\\\.\\", "//?/", "//./")

_WORKSPACE_RELATIVE = Path("LocalZero") / "workspace"


@dataclass(frozen=True, slots=True)
class Contained:
    """The path resolved, and it is inside one of the allowed roots.

    ``path`` is the canonical form, and it is what every later step must use. The raw input is not
    carried forward: whatever the caller wrote, the operation and the approval dialog are about this.
    """

    path: Path
    root: Path


@dataclass(frozen=True, slots=True)
class Refused:
    """The path was refused, naming the rule rather than echoing the value.

    Same discipline as ipc/session.py: a rejected input has no readable fields, and that includes
    for logging. The audit log records an ``args_hash``, never the argument.
    """

    rule: str
    reason: str


PathCheck = Contained | Refused


def workspace_root() -> Path:
    """``%LOCALAPPDATA%\\LocalZero\\workspace``.

    Outside the repository on purpose. A writable root next to the system's own source would make
    red line 8 - the system never writes to its own source - a matter of intention rather than of
    structure.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; Local Zero has no workspace root without it")

    return Path(local_app_data) / _WORKSPACE_RELATIVE


def resolve_within(raw: str, allowed_roots: Sequence[Path]) -> PathCheck:
    """Canonicalise ``raw`` and require the result to sit inside one of ``allowed_roots``."""
    rejection = _refuse_on_inspection(raw)
    if rejection is not None:
        return rejection

    candidate = Path(raw)
    # The leaf is resolved separately from its parent because a file being created does not exist
    # yet, and resolve() only canonicalises components that do. Resolving the parent gets the real
    # directory - links followed, `..` collapsed, case restored - and the validated name is joined
    # onto it.
    staged = candidate.parent.resolve(strict=False) / candidate.name

    # ...and then the whole thing is resolved again, because the leaf itself may be a link. Trusting
    # a resolved parent alone would let a file named innocently inside the root hand back its target
    # outside the root.
    final = staged.resolve(strict=False)

    for root in allowed_roots:
        resolved_root = root.resolve(strict=False)
        if _is_inside(staged, resolved_root) and _is_inside(final, resolved_root):
            return Contained(path=final, root=resolved_root)

    return Refused(
        "outside_roots",
        "the path resolves outside every allowed root for this capability",
    )


def _refuse_on_inspection(raw: str) -> Refused | None:
    """The forms refused without consulting the filesystem."""
    if not raw or not raw.strip():
        return Refused("empty_path", "the path is empty")

    if raw.startswith(BYPASS_PREFIXES):
        return Refused(
            "extended_length_prefix",
            "the path uses a prefix that bypasses normalisation, which containment depends on",
        )

    pure = PureWindowsPath(raw)

    if pure.drive.startswith("\\\\") or pure.drive.startswith("//"):
        return Refused("unc_path", "the path is a UNC path and leaves this machine")

    if not pure.is_absolute():
        return Refused(
            "not_absolute",
            "the path is relative, and would resolve against a working directory this process "
            "does not treat as trustworthy input",
        )

    for part in pure.parts[1:]:
        if ":" in part:
            return Refused(
                "alternate_data_stream",
                "a component names an alternate data stream, which is a different object from the "
                "file whose name it wears",
            )

        if part.split(".")[0].upper() in RESERVED_DEVICE_NAMES:
            return Refused("reserved_device_name", "a component names a device rather than a file")

    return None


def _is_inside(path: Path, root: Path) -> bool:
    """Containment on components, case-folded.

    ``is_relative_to`` compares path components, so ``C:\\AllowedEvil`` is not inside ``C:\\Allowed``
    the way a string prefix would have it. ``normcase`` handles Windows being case-insensitive
    without reintroducing a character-level comparison.
    """
    return Path(os.path.normcase(str(path))).is_relative_to(Path(os.path.normcase(str(root))))
