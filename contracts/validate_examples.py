"""Validate every example payload against its contract schema.

This is the mechanical form of the M0 exit criterion "example messages pass the
schema". It is deliberately the only executable file M0 produces besides the
Claude Code guard hook: a contract nobody can run is a contract that drifts.

Files under examples/ MUST validate. Files under examples/rejected/ MUST fail,
and the reason is printed so a reviewer can confirm it failed for the intended
reason rather than by accident.

The schema is chosen by filename prefix: "ipc." -> ipc.schema.json,
"ws." -> ws.schema.json.

Run:
    uv run --with jsonschema python contracts/validate_examples.py

Exit code 0 means every expectation held.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACTS_DIR = Path(__file__).resolve().parent
SCHEMA_BY_PREFIX = {"ipc.": "ipc.schema.json", "ws.": "ws.schema.json"}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def strip_annotations(message: dict) -> dict:
    """Drop documentation-only keys so a rejected example fails for its real reason.

    Returns a new dict; the input is not mutated.
    """
    return {key: value for key, value in message.items() if not key.startswith("_")}


def schema_for(path: Path) -> dict:
    for prefix, schema_name in SCHEMA_BY_PREFIX.items():
        if path.name.startswith(prefix):
            return load_json(CONTRACTS_DIR / schema_name)
    raise ValueError(
        f"{path.name}: cannot tell which contract this belongs to. "
        f"Name it with one of these prefixes: {sorted(SCHEMA_BY_PREFIX)}"
    )


def narrow_to_type(schema: dict, message_type: str | None) -> dict:
    """Point the schema at the single message definition matching message_type.

    Validating against the top-level oneOf collapses every failure into "is not
    valid under any of the given schemas", which cannot distinguish a message
    rejected for the intended reason from one rejected by accident. Selecting the
    branch by its `type` const first makes the error name the offending field.

    Falls back to the whole schema when the type is missing or unrecognised -
    that is itself a rejection worth reporting.
    """
    definitions = schema.get("$defs", {})
    for name, definition in definitions.items():
        const = definition.get("properties", {}).get("type", {}).get("const")
        if const is not None and const == message_type:
            return {"$schema": schema["$schema"], "$defs": definitions, "$ref": f"#/$defs/{name}"}
    return schema


def validator_for(path: Path, message: dict) -> Draft202012Validator:
    schema = schema_for(path)
    return Draft202012Validator(narrow_to_type(schema, message.get("type")))


def first_error(validator: Draft202012Validator, message: dict) -> str | None:
    """Return the most specific validation error, or None when the message is valid."""
    errors = sorted(validator.iter_errors(message), key=lambda e: len(e.absolute_path), reverse=True)
    if not errors:
        return None
    error = errors[0]
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def check(path: Path, *, must_pass: bool) -> bool:
    """Validate one example. Returns True when it behaved as required."""
    message = strip_annotations(load_json(path))
    error = first_error(validator_for(path, message), message)
    label = path.relative_to(CONTRACTS_DIR).as_posix()

    if must_pass and error is None:
        print(f"  PASS   {label}")
        return True
    if must_pass:
        print(f"  FAIL   {label}\n         expected valid, got: {error}")
        return False
    if error is not None:
        print(f"  PASS   {label}\n         rejected as required: {error}")
        return True
    print(f"  FAIL   {label}\n         expected rejection, but the schema accepted it")
    return False


def main() -> int:
    examples = CONTRACTS_DIR / "examples"
    if not examples.is_dir():
        print(f"No examples directory at {examples}", file=sys.stderr)
        return 2

    valid_files = sorted(p for p in examples.glob("*.json"))
    rejected_files = sorted((examples / "rejected").glob("*.json"))

    if not valid_files or not rejected_files:
        print("Expected at least one valid and one rejected example.", file=sys.stderr)
        return 2

    print(f"Valid examples ({len(valid_files)}) - must pass:")
    results = [check(p, must_pass=True) for p in valid_files]

    print(f"\nRejected examples ({len(rejected_files)}) - must fail:")
    results += [check(p, must_pass=False) for p in rejected_files]

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} expectations held.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
