"""Which model layer is selected, remembered between runs.

The sibling of ``trust.py``, and deliberately built the same way, because it protects the same kind
of thing: a switch that only the user may flip.

* **The file sits beside the workspace, never within it** - ``%LOCALAPPDATA%\\LocalZero\\provider.json``
  next to, not inside, ``workspace``. A capability that could write it could move the egress guard
  into Cloud mode, and the boundary would then be a thing the system could open for itself.
* **Every failure to read means Local.** A corrupt file, an unexpected shape, an unknown mode: none
  of those is a reason to guess in the direction that permits outbound traffic. Absence is not
  permission - the same rule ``trust.json`` follows, and docs/SECURITY.md section 11 states it for
  this file specifically: the state before anybody chooses anything is the one that sends nothing.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from local_zero_brain.capabilities.paths import workspace_root
from local_zero_brain.llm.provider import ProviderMode

PROVIDER_FILE_NAME = "provider.json"

MODES: tuple[ProviderMode, ...] = ("local", "cloud")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProviderState:
    mode: ProviderMode
    since: str


class ProviderStore:
    """Reads and writes the selected mode, failing closed on anything it does not understand."""

    __slots__ = ("_path", "_lock")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @staticmethod
    def default_path() -> Path:
        return workspace_root().parent / PROVIDER_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ProviderState:
        """The current selection. Asking is not deciding, so this never creates the file."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ProviderState(mode="local", since=_now())

        if not isinstance(raw, dict):
            return ProviderState(mode="local", since=_now())

        mode = raw.get("mode")
        if mode not in MODES:
            return ProviderState(mode="local", since=_now())

        since = raw.get("since")
        return ProviderState(mode=mode, since=since if isinstance(since, str) else _now())

    def set(self, *, mode: ProviderMode) -> ProviderState:
        """Records a new selection and returns it. The only writer of this file."""
        if mode not in MODES:
            raise ValueError(f"unknown provider mode {mode!r}")

        state = ProviderState(mode=mode, since=_now())

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"mode": state.mode, "since": state.since}, indent=2), encoding="utf-8"
            )

        return state
