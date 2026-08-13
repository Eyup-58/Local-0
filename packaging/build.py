"""Builds the installable package.

    uv run python packaging/build.py

Produces ``dist/LocalZero-<version>/`` and a zip beside it, then inspects what it built - a package
that fails inspection is deleted rather than shipped, because the point of the check is that nobody
has to remember to run it.

**What is in the package and why.** The sidecar is published self-contained, so a target machine
needs no .NET runtime. The UI is the built assets, which the brain serves. The brain ships as source
with ``pyproject.toml`` and ``uv.lock``, and the installer runs ``uv sync --frozen`` - uv fetches the
right Python itself, which is the one prerequisite this package has and the one thing it names.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST = REPOSITORY_ROOT / "dist"

#: Copied wholesale into the package. Everything else is either built or deliberately left out.
SOURCE_ITEMS = ("run.py", "pyproject.toml", "uv.lock")

#: Never packaged. `__pycache__` first: a .pyc records the absolute path of the file it was compiled
#: from, so shipping one would put this machine's directory layout inside the artifact.
EXCLUDED = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", ".venv", "*.pdb")


def version() -> str:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def run(command: list[str], *, cwd: Path) -> None:
    """Every build step, with its output shown. No shell, and no string interpolation."""
    print(f"\n$ {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd, shell=False)
    if result.returncode != 0:
        print(f"\nbuild step failed with code {result.returncode}", file=sys.stderr)
        raise SystemExit(result.returncode)


def publish_sidecar(package: Path) -> None:
    """Self-contained, so the target needs no .NET runtime. No symbols: a .pdb carries build paths."""
    run(
        [
            "dotnet", "publish",
            str(REPOSITORY_ROOT / "system" / "LocalZero.System" / "LocalZero.System.csproj"),
            "-c", "Release",
            "-r", "win-x64",
            "--self-contained", "true",
            "-p:DebugType=none",
            "-p:DebugSymbols=false",
            "-o", str(package / "system"),
        ],
        cwd=REPOSITORY_ROOT,
    )


def build_ui(package: Path) -> None:
    # `npm run build` would need a shell - npm is a .cmd, and CreateProcess does not run those.
    # These are the two commands that script is, called through node directly, which is the same
    # thing bench/run_stack.py does and keeps this script free of a shell.
    ui = REPOSITORY_ROOT / "ui"
    run(["node", "node_modules/typescript/bin/tsc", "--noEmit"], cwd=ui)
    run(["node", "node_modules/vite/bin/vite.js", "build"], cwd=ui)

    built = REPOSITORY_ROOT / "ui" / "dist"
    if not (built / "index.html").is_file():
        print(f"the UI build produced no index.html in {built}", file=sys.stderr)
        raise SystemExit(1)

    shutil.copytree(built, package / "ui" / "dist", ignore=EXCLUDED)


def copy_brain(package: Path) -> None:
    shutil.copytree(REPOSITORY_ROOT / "brain", package / "brain", ignore=EXCLUDED)
    for item in SOURCE_ITEMS:
        shutil.copy2(REPOSITORY_ROOT / item, package / item)

    for script in ("install.ps1", "uninstall.ps1", "install.cmd", "README.txt"):
        shutil.copy2(REPOSITORY_ROOT / "packaging" / script, package / script)


def inspect(package: Path) -> bool:
    """The artifact check, run as part of the build rather than as a step to remember."""
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "packaging" / "inspect_artifact.py"), str(package)],
        cwd=REPOSITORY_ROOT,
        shell=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="keep a package that failed inspection, so its findings can be looked at",
    )
    arguments = parser.parse_args()

    package = DIST / f"LocalZero-{version()}"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    publish_sidecar(package)
    build_ui(package)
    copy_brain(package)

    if not inspect(package) and not arguments.keep_failed:
        shutil.rmtree(package)
        print("\npackage deleted; it did not pass inspection", file=sys.stderr)
        return 1

    archive = shutil.make_archive(str(package), "zip", root_dir=DIST, base_dir=package.name)
    size_mb = Path(archive).stat().st_size / (1024 * 1024)
    print(f"\n{package.relative_to(REPOSITORY_ROOT).as_posix()}")
    print(f"{Path(archive).relative_to(REPOSITORY_ROOT).as_posix()}  ({size_mb:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
