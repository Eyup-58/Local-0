"""Path containment - the step guards like this actually fail at.

docs/SECURITY.md section 4 step 3: every path argument is resolved to an absolute canonical form and
then checked to be inside one of ``allowed_roots``, on the resolved path and never on the input
string. These tests exist to make that claim falsifiable.

Each refusal here is a real filesystem construction, not a string the test hopes is representative.
A junction is a junction on disk; a symlink is a symlink on disk. The bug being guarded against is
precisely that the string looks contained while the filesystem disagrees, so a test working on
strings alone would pass against the broken implementation.

**These tests were checked against two mutations of the implementation**, because a guard test that
passes without the guard is worse than no test:

* replacing component containment with a naive string prefix - caught only by
  ``test_a_sibling_directory_sharing_the_prefix_is_refused``, because everything else has already
  been resolved out of the root by then and a prefix check refuses those correctly too;
* removing resolution and checking the input string - caught by the junction, ``..`` and
  ``<root>\\..\\Windows`` cases, and by none of the others.

Neither mutation survives the suite, and the two are killed by disjoint sets of tests. Deleting any
of those four cases as redundant would let one of the mutations back in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from local_zero_brain.capabilities.paths import Contained, Refused, resolve_within, workspace_root


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """An allowed root and a directory outside it, both real."""
    allowed = tmp_path / "workspace"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    return allowed, outside


def make_junction(link: Path, target: Path) -> None:
    """A directory junction, which Windows permits without elevation."""
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)


def make_symlink(link: Path, target: Path) -> None:
    """A directory symlink, which needs SeCreateSymbolicLinkPrivilege - i.e. Developer Mode.

    Skipped rather than faked when unavailable. A test that silently substitutes a junction here
    would report coverage of a case it never exercised.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(
            "cannot create a symlink without SeCreateSymbolicLinkPrivilege. Turn on Developer Mode "
            f"(Settings > System > For developers) so this case is actually tested. ({error})"
        )


# --- the path that should work ---------------------------------------------------------------


def test_an_absolute_path_inside_the_root_is_contained(roots: tuple[Path, Path]) -> None:
    allowed, _ = roots
    target = allowed / "note.txt"
    target.write_text("hello", encoding="utf-8")

    result = resolve_within(str(target), [allowed])

    assert isinstance(result, Contained)
    assert result.path == target.resolve()


def test_a_file_that_does_not_exist_yet_is_contained(roots: tuple[Path, Path]) -> None:
    """write_text_file legitimately targets a file that is not there yet, so resolution cannot
    require the leaf to exist."""
    allowed, _ = roots

    result = resolve_within(str(allowed / "new.txt"), [allowed])

    assert isinstance(result, Contained)


def test_dotdot_that_stays_inside_the_root_is_contained(roots: tuple[Path, Path]) -> None:
    """`..` is not itself the offence. Escaping is."""
    allowed, _ = roots
    (allowed / "sub").mkdir()

    result = resolve_within(str(allowed / "sub" / ".." / "note.txt"), [allowed])

    assert isinstance(result, Contained)
    assert result.path == (allowed / "note.txt").resolve()


def test_a_root_given_in_the_wrong_case_still_matches(roots: tuple[Path, Path]) -> None:
    """Windows paths are case-insensitive; a containment check that is not would refuse legitimate
    input and, worse, could be worked around by changing case."""
    allowed, _ = roots

    result = resolve_within(str(allowed).upper() + os.sep + "note.txt", [allowed])

    assert isinstance(result, Contained)


# --- the four refusals ROADMAP M2 names ------------------------------------------------------


def test_dotdot_traversal_out_of_the_root_is_refused(roots: tuple[Path, Path]) -> None:
    """The case ROADMAP M2 names explicitly: <allowed_root>\\..\\Windows.

    Prefix-matching the raw string accepts this, because the string does start with the root.
    """
    allowed, _ = roots

    result = resolve_within(str(allowed / ".." / "Windows"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


def test_an_absolute_path_outside_the_roots_is_refused(roots: tuple[Path, Path]) -> None:
    allowed, outside = roots

    result = resolve_within(str(outside / "secret.txt"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


def test_a_junction_crossing_the_boundary_is_refused(roots: tuple[Path, Path]) -> None:
    """Measured: Path.resolve() follows a junction, so the resolved path lands outside and the
    containment check catches it. The naive string check does not."""
    allowed, outside = roots
    make_junction(allowed / "escape", outside)

    result = resolve_within(str(allowed / "escape" / "secret.txt"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


def test_a_symlink_crossing_the_boundary_is_refused(roots: tuple[Path, Path]) -> None:
    allowed, outside = roots
    make_symlink(allowed / "slink", outside)

    result = resolve_within(str(allowed / "slink" / "secret.txt"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


def test_a_symlink_at_the_leaf_is_refused(roots: tuple[Path, Path]) -> None:
    """The subtle one. Resolving only the parent and trusting the final component would let a link
    named innocently inside the root hand back a file outside it."""
    allowed, outside = roots
    link = allowed / "innocent.txt"
    try:
        os.symlink(outside / "secret.txt", link)
    except OSError as error:
        pytest.skip(f"symlink creation needs Developer Mode ({error})")

    result = resolve_within(str(link), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


# --- the sibling-prefix bug ------------------------------------------------------------------


def test_a_sibling_directory_sharing_the_prefix_is_refused(tmp_path: Path) -> None:
    """`C:\\Allowed` is a string prefix of `C:\\AllowedEvil`. Component boundaries are not string
    boundaries, which is why containment is is_relative_to and not startswith."""
    allowed = tmp_path / "workspace"
    sibling = tmp_path / "workspace_evil"
    allowed.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("x", encoding="utf-8")

    result = resolve_within(str(sibling / "secret.txt"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "outside_roots"


# --- Windows-specific forms, refused before the filesystem is touched -------------------------


def test_a_relative_path_is_refused(roots: tuple[Path, Path]) -> None:
    """A relative path resolves against the current working directory, which is not something this
    process is willing to trust as an input to a file operation."""
    allowed, _ = roots

    result = resolve_within("note.txt", [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "not_absolute"


def test_an_alternate_data_stream_is_refused(roots: tuple[Path, Path]) -> None:
    """NTFS lets `note.txt:hidden` address a second stream on the same file. It is a different
    object with the same visible name, so it is refused rather than reasoned about."""
    allowed, _ = roots

    result = resolve_within(str(allowed / "note.txt") + ":hidden", [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "alternate_data_stream"


@pytest.mark.parametrize("name", ["CON", "NUL", "COM1", "lpt9", "nul.txt"])
def test_a_reserved_device_name_is_refused(roots: tuple[Path, Path], name: str) -> None:
    """These names address devices, not files, wherever they appear in a path."""
    allowed, _ = roots

    result = resolve_within(str(allowed / name), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "reserved_device_name"


def test_a_unc_path_is_refused(roots: tuple[Path, Path]) -> None:
    """A UNC path leaves the machine. SECURITY.md section 10 puts remote access out of scope."""
    allowed, _ = roots

    result = resolve_within(r"\\server\share\secret.txt", [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "unc_path"


def test_an_extended_length_prefix_is_refused(roots: tuple[Path, Path]) -> None:
    r"""`\\?\` tells Win32 to skip normalisation entirely, which is the one thing containment
    depends on."""
    allowed, _ = roots

    result = resolve_within("\\\\?\\" + str(allowed / "note.txt"), [allowed])

    assert isinstance(result, Refused)
    assert result.rule == "extended_length_prefix"


def test_an_empty_path_is_refused(roots: tuple[Path, Path]) -> None:
    allowed, _ = roots

    result = resolve_within("", [allowed])

    assert isinstance(result, Refused)


# --- refusals do not leak the value ------------------------------------------------------------


def test_the_workspace_root_is_absolute_and_outside_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Red line 8 - the system never writes to its own source - is meant to hold structurally rather
    than by intention, which requires the writable root to be somewhere the source is not."""
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    root = workspace_root()

    assert root.is_absolute()
    assert root == Path(r"C:\Users\someone\AppData\Local\LocalZero\workspace")


def test_a_missing_localappdata_is_a_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a fallback to the working directory. A guessed workspace root is a writable root nobody
    chose, and the guard would then be containing paths inside it."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        workspace_root()


def test_a_refusal_names_the_rule_without_echoing_the_path(roots: tuple[Path, Path]) -> None:
    """The same discipline as ipc/session.py: a rejected input has no readable fields, and that
    includes for logging. The audit records an args_hash, not the argument."""
    allowed, outside = roots

    result = resolve_within(str(outside / "secret.txt"), [allowed])

    assert isinstance(result, Refused)
    assert "secret.txt" not in result.reason
    assert str(outside) not in result.reason
