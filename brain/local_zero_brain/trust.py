"""Trust mode: the state behind the button that turns approval off.

Chosen deliberately by the user, with the consequence stated at the time: trust mode bypasses the
approval gate for **every** invocation regardless of ``side_effect`` or ``origin``, and it persists
across restarts. docs/SECURITY.md section 5 says so in the same words rather than describing a
control the product lets you switch off.

What it does **not** bypass is the guard. The name whitelist, the argument schema and path
canonicalisation are steps 1-3; approval is step 4. Trust mode skips the gate, not the guard - and
that distinction is what makes this module's central property possible:

**The state file lives outside every capability's ``allowed_roots``.** ``%LOCALAPPDATA%\\LocalZero``
holds ``trust.json`` next to - not inside - ``workspace``. A ``write_text_file`` invocation aimed at
it is refused at step 3, which still runs when the button is on. So the file is protected by
containment rather than by approval, and turning approval off does not open the door to turning
approval off.

Every failure to read means **off**. A corrupt file, an unexpected shape, a bare value: none of those
is a reason to guess in the permissive direction. "Unknown" resolving to "approval is disabled" is
the worst default available here.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from local_zero_brain.capabilities.paths import workspace_root

#: Sits beside the workspace, never within it. See the module docstring - this placement is the
#: control, not a filing preference.
TRUST_FILE_NAME = "trust.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TrustState:
    """Whether approval is currently bypassed, and when that last changed."""

    enabled: bool
    since: str


class TrustStore:
    """Reads and writes the trust state, failing closed on anything it does not understand."""

    __slots__ = ("_path", "_lock")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @staticmethod
    def default_path() -> Path:
        """``%LOCALAPPDATA%\\LocalZero\\trust.json`` - a sibling of the workspace, not a child.

        If this ever moves inside the workspace, a capability could write it, and with trust mode on
        approval would not be there to stop that. A test asserts the two are not nested.
        """
        return workspace_root().parent / TRUST_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> TrustState:
        """The current state. Asking is not deciding, so this never creates the file."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return TrustState(enabled=False, since=_now())

        # A bare `true`, a list, a string - anything that is not the shape written by set() means the
        # file cannot be interpreted, and an uninterpretable file is not permission.
        if not isinstance(raw, dict):
            return TrustState(enabled=False, since=_now())

        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            return TrustState(enabled=False, since=_now())

        since = raw.get("since")
        return TrustState(enabled=enabled, since=since if isinstance(since, str) else _now())

    def set(self, *, enabled: bool) -> TrustState:
        """Records a new state and returns it. The only writer of this file."""
        state = TrustState(enabled=enabled, since=_now())

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"enabled": state.enabled, "since": state.since}, indent=2),
                encoding="utf-8",
            )

        return state
