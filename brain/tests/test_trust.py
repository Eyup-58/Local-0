"""Trust mode: the state behind the button that turns approval off.

The user chose this deliberately, with the consequence stated: trust mode bypasses approval for
every invocation regardless of side_effect or origin, and it persists across restarts. See
docs/SECURITY.md section 5.

What these tests are actually protecting is the *other* half of that decision - that the button is
the user's and nothing else can press it. Two properties carry that:

* the state file lives outside every capability's allowed_roots, so step 3 of the guard refuses to
  resolve a path to it at all - containment protects it, not approval, which is why it still holds
  when the button is on;
* every failure to read it means **off**. A corrupt file, an unreadable file, a file holding
  something unexpected: none of those is a reason to guess in the permissive direction.
"""

from __future__ import annotations

import json
from pathlib import Path

from local_zero_brain.trust import TrustState, TrustStore


def test_trust_is_off_when_no_state_file_exists(tmp_path: Path) -> None:
    """A fresh install has approval on. Nothing is bypassed until somebody chooses it."""
    store = TrustStore(tmp_path / "trust.json")

    assert store.load().enabled is False


def test_enabling_persists_across_a_new_store(tmp_path: Path) -> None:
    """Persistence is the behaviour the user asked for: flip it once, it stays flipped."""
    path = tmp_path / "trust.json"
    TrustStore(path).set(enabled=True)

    assert TrustStore(path).load().enabled is True


def test_disabling_persists_too(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    store = TrustStore(path)
    store.set(enabled=True)

    store.set(enabled=False)

    assert TrustStore(path).load().enabled is False


def test_setting_records_when_it_changed(tmp_path: Path) -> None:
    store = TrustStore(tmp_path / "trust.json")

    state = store.set(enabled=True)

    assert state.since.endswith("Z")


def test_a_corrupt_state_file_reads_as_off(tmp_path: Path) -> None:
    """Not an exception, and above all not on.

    A file that will not parse is a file whose contents are unknown, and "unknown" resolving to
    "approval is disabled" is the single worst default available here.
    """
    path = tmp_path / "trust.json"
    path.write_text("{not json at all", encoding="utf-8")

    assert TrustStore(path).load().enabled is False


def test_a_state_file_of_the_wrong_shape_reads_as_off(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"enabled": "yes please"}), encoding="utf-8")

    assert TrustStore(path).load().enabled is False


def test_a_state_file_holding_a_bare_value_reads_as_off(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    path.write_text("true", encoding="utf-8")

    assert TrustStore(path).load().enabled is False


def test_the_default_location_is_outside_the_workspace(monkeypatch) -> None:
    """The property that keeps the button the user's.

    Every capability's allowed_roots is the workspace. If the state file were inside it, a
    write_text_file invocation could turn trust mode on - and with trust mode on, approval would not
    be there to stop it. Containment is what protects this file, so it has to sit where containment
    says no.
    """
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    from local_zero_brain.capabilities.paths import workspace_root

    location = TrustStore.default_path()

    assert location == Path(r"C:\Users\someone\AppData\Local\LocalZero\trust.json")
    assert not location.is_relative_to(workspace_root())


def test_the_state_is_immutable(tmp_path: Path) -> None:
    state = TrustStore(tmp_path / "trust.json").load()

    try:
        state.enabled = True  # type: ignore[misc]
    except Exception:
        return

    raise AssertionError("TrustState must be frozen")


def test_reading_does_not_create_the_file(tmp_path: Path) -> None:
    """Loading is a question, not a decision. Nothing is written until the user presses the button."""
    path = tmp_path / "trust.json"

    TrustStore(path).load()

    assert not path.exists()
