"""Hold the brain's Pydantic models against the schemas they mirror.

This is the drift guard. The models are a second representation of the contract, and a second
representation is a second place to be wrong. Running every checked-in example through them means
the models cannot quietly diverge from the schemas: the valid examples must parse, and the
rejected ones must fail.

The rejected half is the more useful one. It is what turns "schema-invalid messages are dropped"
from an assertion into a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from conftest import load_example, rejected_examples, valid_examples
from local_zero_brain.contracts.ipc import IPC_MESSAGE_ADAPTER
from local_zero_brain.contracts.ws import WS_MESSAGE_ADAPTER

#: The schema a file belongs to is chosen by filename prefix, exactly as
#: contracts/validate_examples.py does it.
ADAPTER_BY_PREFIX: dict[str, TypeAdapter] = {
    "ipc.": IPC_MESSAGE_ADAPTER,
    "ws.": WS_MESSAGE_ADAPTER,
}


def adapter_for(path: Path) -> TypeAdapter:
    for prefix, adapter in ADAPTER_BY_PREFIX.items():
        if path.name.startswith(prefix):
            return adapter
    raise ValueError(
        f"{path.name}: cannot tell which contract this belongs to. "
        f"Name it with one of these prefixes: {sorted(ADAPTER_BY_PREFIX)}"
    )


def test_there_are_examples_to_check() -> None:
    """A glob that silently matches nothing would make every test below vacuously pass."""
    assert valid_examples(), "no valid examples found under contracts/examples"
    assert rejected_examples(), "no rejected examples found under contracts/examples/rejected"


@pytest.mark.parametrize("path", valid_examples(), ids=lambda p: p.name)
def test_valid_example_parses(path: Path) -> None:
    message = load_example(path)

    parsed = adapter_for(path).validate_python(message)

    assert parsed.type == message["type"]


@pytest.mark.parametrize("path", rejected_examples(), ids=lambda p: p.name)
def test_rejected_example_is_refused(path: Path) -> None:
    message = load_example(path)

    with pytest.raises(ValidationError):
        adapter_for(path).validate_python(message)


@pytest.mark.parametrize("path", rejected_examples(), ids=lambda p: p.name)
def test_rejected_example_is_refused_for_its_stated_reason(path: Path) -> None:
    """Each rejected example carries a note saying why it must fail.

    Asserting only that it failed would pass just as happily if it failed for an unrelated reason -
    a typo in the fixture, say - which would quietly retire the case the example exists to cover.
    """
    raw = load_example(path)
    expectations = {
        "ipc.elevated-true.json": "elevated",
        "ipc.unavailable-without-reason.json": "unavailable_reason",
        "ipc.unknown-field.json": "exec",
        "ipc.per-core-out-of-range.json": "per_core_percent",
        "ws.unsupported-version.json": "v",
        "ws.approval-unknown-decision.json": "decision",
        "ws.approval-nested-args.json": "resolved_args",
        "ws.credential-set-empty.json": "key",
        "ws.provider-status-carrying-the-key.json": "key",
        "ws.memory-reindex-with-a-path.json": "vault",
    }
    offending_field = expectations.get(path.name)
    if offending_field is None:
        pytest.fail(
            f"{path.name} has no recorded reason for rejection. Add one here so the example "
            f"cannot start failing for an unintended reason without anyone noticing."
        )

    with pytest.raises(ValidationError) as caught:
        adapter_for(path).validate_python(raw)

    assert offending_field in str(caught.value)
