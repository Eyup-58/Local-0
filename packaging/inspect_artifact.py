"""Inspects a built package for anything belonging to the machine that built it.

    uv run python packaging/inspect_artifact.py dist/LocalZero-0.1.0

M7 exit criterion: "No key, no vault path and no absolute path from this machine is baked into the
artifact. Verified by inspecting the built package, not by trusting the source." So this reads the
bytes that would actually ship, and it is run by build.py rather than left as a step to remember.

Both UTF-8 and UTF-16LE forms of every needle are searched: .NET strings are UTF-16, and a path
compiled into a managed assembly would be invisible to a UTF-8 scan.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: What must be in the package for it to be a package at all.
REQUIRED = ("run.py", "brain", "ui/dist/index.html", "system/LocalZero.System.exe", "uv.lock")

#: What must never be: compiled Python records the absolute path it was built from, symbols carry
#: build paths, and a virtual environment is full of them.
FORBIDDEN_PATTERNS = ("__pycache__", "*.pyc", "*.pdb", ".venv", ".env", "*.key", "*.pfx")

#: Key shapes, by the prefixes their issuers use. A hit is a finding, not a guess about validity.
KEY_SHAPES = re.compile(
    rb"(sk-[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_\-]{20,}|gsk_[A-Za-z0-9]{20,}"
    rb"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,})"
)

#: Read in chunks so a 70 MiB self-contained runtime does not become a 70 MiB string.
CHUNK_BYTES = 4 * 1024 * 1024


def machine_needles() -> dict[str, str]:
    """Strings that would identify this machine if they appeared in the artifact."""
    needles = {
        "the build directory": str(REPOSITORY_ROOT),
        "the build directory (posix form)": REPOSITORY_ROOT.as_posix(),
        "a user profile path": str(Path.home()),
        "the user profile directory": "C:\\Users\\",
    }

    vault = os.environ.get("OBSIDIAN_VAULT_PATH")
    if vault:
        needles["the vault path"] = vault

    return needles


def files(package: Path) -> Iterator[Path]:
    return (path for path in package.rglob("*") if path.is_file())


def scan(path: Path, needles: dict[str, str]) -> list[str]:
    """Every needle, in both encodings, plus the key shapes. Returns what was found."""
    encoded = {
        label: (text.encode("utf-8"), text.encode("utf-16-le"))
        for label, text in needles.items()
    }
    found: list[str] = []

    with path.open("rb") as handle:
        previous = b""
        while chunk := handle.read(CHUNK_BYTES):
            window = previous + chunk
            for label, (utf8, utf16) in encoded.items():
                if label in found:
                    continue
                if utf8 in window or utf16 in window:
                    found.append(label)

            if (match := KEY_SHAPES.search(window)) and "a key-shaped string" not in found:
                found.append(f"a key-shaped string ({match.group(0)[:6].decode('ascii')}...)")

            # Overlap, so a needle straddling a chunk boundary is not missed.
            previous = window[-4096:]

    return found


def check_shape(package: Path) -> list[str]:
    problems = [f"missing: {item}" for item in REQUIRED if not (package / item).exists()]

    for pattern in FORBIDDEN_PATTERNS:
        for path in package.rglob(pattern):
            problems.append(f"must not ship: {path.relative_to(package).as_posix()}")

    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    package = Path(sys.argv[1]).resolve()
    if not package.is_dir():
        print(f"not a directory: {package}", file=sys.stderr)
        return 2

    print(f"inspecting {package}")
    problems = check_shape(package)

    needles = machine_needles()
    scanned = 0
    for path in files(package):
        scanned += 1
        for finding in scan(path, needles):
            problems.append(f"{path.relative_to(package).as_posix()}: contains {finding}")

    print(f"  {scanned} files scanned for {len(needles)} machine strings and key shapes")

    if problems:
        print("\nFAIL - the artifact carries something it must not:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("  clean: no key, no vault path, no path from this machine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
