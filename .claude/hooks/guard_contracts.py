"""PreToolUse guard: stop the contracts from drifting away from their documentation.

Contract drift is the most likely way a three-layer project breaks quietly. A schema
gets a field, one layer is updated, docs/CONTRACTS.md is not, and six weeks later two
layers disagree about a message shape that the documentation describes correctly and
neither implements.

So: editing anything under contracts/ requires docs/CONTRACTS.md to be touched in the
same working-tree change. When it has not been, this hook escalates the edit to a human
prompt explaining what else is required. It does not block outright - there are
legitimate cases (fixing a typo in a description) - it makes skipping the doc a
deliberate choice rather than an oversight.

Contract: reads the PreToolUse payload on stdin, writes a JSON decision on stdout.
Any internal failure exits 0 silently. A broken guard must never be able to wedge the
session - it is a seatbelt, not a lock.

Wired up by .claude/settings.json. See docs/CONTRACTS.md section 6.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WATCHED_DIR = "contracts"
COMPANION_DOC = "docs/CONTRACTS.md"


def read_event() -> dict:
    return json.loads(sys.stdin.read() or "{}")


def target_path(event: dict) -> str:
    return event.get("tool_input", {}).get("file_path", "")


def repo_root(event: dict) -> Path:
    return Path(event.get("cwd") or Path.cwd())


def is_contract_file(path_text: str, root: Path) -> bool:
    """True when the edit targets contracts/, excluding its own tooling and examples.

    Examples and the validator move with the schema; it is the schema and its
    documentation that must stay in step.
    """
    if not path_text:
        return False
    try:
        relative = Path(path_text).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False

    parts = relative.parts
    if not parts or parts[0] != WATCHED_DIR:
        return False
    return relative.suffix == ".json" and "examples" not in parts


def doc_already_touched(root: Path) -> bool:
    """True when docs/CONTRACTS.md has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", COMPANION_DOC],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # No git, or git is unwell. Assume the doc was touched rather than nag wrongly.
        return True
    return bool(result.stdout.strip())


def ask(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def main() -> int:
    try:
        event = read_event()
        root = repo_root(event)
        path_text = target_path(event)

        if not is_contract_file(path_text, root):
            return 0
        if doc_already_touched(root):
            return 0

        ask(
            f"This edits {Path(path_text).name}, which is a source-of-truth contract.\n"
            f"{COMPANION_DOC} has no pending changes, so the two are about to diverge.\n\n"
            "Before continuing, confirm that you will also:\n"
            f"  1. update {COMPANION_DOC} in this same change\n"
            "  2. add or update an example under contracts/examples/\n"
            "  3. run: uv run --with jsonschema python contracts/validate_examples.py\n"
            "  4. keep the contract change as its own commit\n\n"
            "A breaking change also requires incrementing the schema's `v`."
        )
        return 0
    except Exception:
        # A guard that crashes must not take the session with it.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
