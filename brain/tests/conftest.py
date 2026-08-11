"""Shared test fixtures.

The contract examples are read from contracts/ in place rather than copied into the test tree. A
copy is a second source of truth, and the first time it goes stale the suite passes against a
contract that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPOSITORY_ROOT / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
REJECTED_DIR = EXAMPLES_DIR / "rejected"


def strip_annotations(message: dict) -> dict:
    """Drop documentation-only keys so a rejected example fails for its real reason.

    Returns a new dict; the input is not mutated. Mirrors the helper in
    contracts/validate_examples.py, which the rejected examples are written against.
    """
    return {key: value for key, value in message.items() if not key.startswith("_")}


def load_example(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return strip_annotations(json.load(handle))


def ipc_hello() -> dict:
    """A valid system hello, read from the checked-in example.

    Tests build their cases by copying this rather than hand-writing a message. A hand-written
    fixture drifts from the contract silently; this one cannot, because the same file is validated
    against the schema by contracts/validate_examples.py.
    """
    return load_example(EXAMPLES_DIR / "ipc.hello.json")


def ipc_telemetry_sample() -> dict:
    return load_example(EXAMPLES_DIR / "ipc.telemetry-sample.json")


def valid_examples() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.json"))


def rejected_examples() -> list[Path]:
    return sorted(REJECTED_DIR.glob("*.json"))
