"""The artifact inspector, which is the instrument behind an M7 exit criterion.

"No key, no vault path and no absolute path from this machine is baked into the artifact, verified
by inspecting the built package" is only worth as much as the check that verifies it. A scanner that
never finds anything and a clean artifact look identical from the outside, so these cases plant each
kind of finding and require it to be caught.

Lives under brain/tests because that is where pytest looks (`testpaths` in pyproject.toml), not
because it is part of the brain.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_inspector():
    spec = importlib.util.spec_from_file_location(
        "local_zero_inspect", REPOSITORY_ROOT / "packaging" / "inspect_artifact.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inspector = _load_inspector()


def make_package(root: Path) -> Path:
    """The minimum shape the inspector requires, so a finding is the only thing that can fail."""
    package = root / "LocalZero-0.0.0"
    (package / "brain").mkdir(parents=True)
    (package / "ui" / "dist").mkdir(parents=True)
    (package / "system").mkdir(parents=True)

    (package / "run.py").write_text("# launcher\n", encoding="utf-8")
    (package / "uv.lock").write_text("# lock\n", encoding="utf-8")
    (package / "ui" / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (package / "system" / "LocalZero.System.exe").write_bytes(b"MZ")
    return package


def findings(package: Path) -> list[str]:
    problems = inspector.check_shape(package)
    needles = inspector.machine_needles()
    for path in inspector.files(package):
        problems.extend(f"{path.name}: {found}" for found in inspector.scan(path, needles))
    return problems


def test_a_package_with_nothing_planted_in_it_passes(tmp_path: Path) -> None:
    assert findings(make_package(tmp_path)) == []


def test_the_build_directory_is_caught(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "brain" / "leak.txt").write_text(f"built from {REPOSITORY_ROOT}", encoding="utf-8")

    assert any("the build directory" in problem for problem in findings(package))


def test_a_build_path_hidden_in_utf16_is_caught(tmp_path: Path) -> None:
    """A path compiled into a .NET assembly is UTF-16, and a UTF-8 scan would walk past it."""
    package = make_package(tmp_path)
    (package / "system" / "assembly.dll").write_bytes(str(REPOSITORY_ROOT).encode("utf-16-le"))

    assert any("the build directory" in problem for problem in findings(package))


def test_a_key_shaped_string_is_caught(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "brain" / "config.json").write_text(
        '{"key": "sk-abcdefghijklmnopqrstuvwxyz012345"}',  # pragma: allowlist secret
        encoding="utf-8",
    )

    assert any("key-shaped" in problem for problem in findings(package))


def test_a_needle_split_across_two_read_chunks_is_caught(tmp_path: Path) -> None:
    """The scanner reads in chunks; without the overlap this file would look clean."""
    package = make_package(tmp_path)
    needle = str(REPOSITORY_ROOT).encode("utf-8")
    padding = b"." * (inspector.CHUNK_BYTES - (len(needle) // 2))
    (package / "brain" / "big.bin").write_bytes(padding + needle + b".")

    assert any("the build directory" in problem for problem in findings(package))


def test_compiled_python_is_refused_because_it_records_where_it_was_built(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "brain" / "__pycache__").mkdir()
    (package / "brain" / "__pycache__" / "server.cpython-313.pyc").write_bytes(b"\x00")

    assert any("must not ship" in problem for problem in findings(package))


def test_a_package_missing_what_makes_it_a_package_is_refused(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "ui" / "dist" / "index.html").unlink()

    assert any("missing: ui/dist/index.html" in problem for problem in findings(package))


def test_the_uninstaller_removes_the_credential_the_product_actually_stores() -> None:
    """The uninstaller carries the credential target as a literal, because PowerShell cannot import
    a Python constant. This is the thread that keeps the two from drifting apart: if GEMINI_TARGET
    ever moves, an uninstall would quietly stop removing the stored key and the user would believe
    it was gone.
    """
    from local_zero_brain.credentials import GEMINI_TARGET

    uninstaller = (REPOSITORY_ROOT / "packaging" / "uninstall.ps1").read_text(encoding="utf-8")

    assert f'"{GEMINI_TARGET}"' in uninstaller
