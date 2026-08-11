"""The audit log - and specifically what it must not contain.

docs/SECURITY.md section 9: every decision is recorded, including the denials, especially the
denials. The field that carries the most design in it is ``args_hash``: the arguments themselves can
hold paths and file content, so what is written down is a hash of them and never the values.

The log is gitignored (``/logs``) because once ingestion exists it will contain attacker-controlled
strings, and committing it would publish them and make them look like repository content.
"""

from __future__ import annotations

import json
from pathlib import Path

from local_zero_brain.audit import AuditLog, AuditRecord


def make_record(**overrides: object) -> AuditRecord:
    defaults: dict[str, object] = {
        "origin": "user_direct",
        "capability": "read_text_file",
        "resolved_args": {"path": "C:/workspace/note.txt"},
        "affected_paths": ("C:/workspace/note.txt",),
        "side_effect": "read",
        "decision": "allowed",
        "reason": "",
    }
    return AuditRecord(**{**defaults, **overrides})  # type: ignore[arg-type]


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_decision_is_written_as_one_json_line(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record())

    lines = read_lines(tmp_path / "audit.jsonl")
    assert len(lines) == 1
    assert lines[0]["capability"] == "read_text_file"
    assert lines[0]["decision"] == "allowed"


def test_the_log_is_append_only(tmp_path: Path) -> None:
    """A log that rewrites is a log an operation can remove itself from."""
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record())
    log.record(make_record(decision="denied_guard", reason="outside_roots"))

    lines = read_lines(tmp_path / "audit.jsonl")
    assert len(lines) == 2
    assert [entry["decision"] for entry in lines] == ["allowed", "denied_guard"]


def test_denials_are_recorded_too(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record(decision="denied_guard", reason="path resolves outside every allowed root"))

    entry = read_lines(tmp_path / "audit.jsonl")[0]
    assert entry["decision"] == "denied_guard"
    assert entry["reason"]


def test_the_arguments_are_hashed_not_written(tmp_path: Path) -> None:
    """The load-bearing assertion of this module."""
    log = AuditLog(tmp_path / "audit.jsonl")
    secret_path = "C:/Users/someone/Documents/tax-return-2025.pdf"

    log.record(make_record(resolved_args={"path": secret_path}, affected_paths=()))

    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert secret_path not in raw
    assert "tax-return" not in raw

    entry = json.loads(raw)
    assert len(entry["args_hash"]) == 64  # sha256 hex
    assert "resolved_args" not in entry


def test_the_same_arguments_hash_the_same_regardless_of_key_order(tmp_path: Path) -> None:
    """Otherwise two records of the identical operation are not comparable, and the log stops being
    useful for spotting a repeated attempt."""
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record(resolved_args={"path": "C:/a", "content": "x"}))
    log.record(make_record(resolved_args={"content": "x", "path": "C:/a"}))

    hashes = {entry["args_hash"] for entry in read_lines(tmp_path / "audit.jsonl")}
    assert len(hashes) == 1


def test_different_arguments_hash_differently(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record(resolved_args={"path": "C:/a"}))
    log.record(make_record(resolved_args={"path": "C:/b"}))

    hashes = {entry["args_hash"] for entry in read_lines(tmp_path / "audit.jsonl")}
    assert len(hashes) == 2


def test_affected_paths_are_recorded_in_full(tmp_path: Path) -> None:
    """These are computed by the backend, not narrated by anything, and the human approving an
    operation in M3 is shown exactly this list. It is the one place paths do appear."""
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record(affected_paths=("C:/workspace/a.txt", "C:/workspace/b.txt")))

    entry = read_lines(tmp_path / "audit.jsonl")[0]
    assert entry["affected_paths"] == ["C:/workspace/a.txt", "C:/workspace/b.txt"]


def test_the_timestamp_is_rfc3339_utc(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")

    log.record(make_record())

    assert read_lines(tmp_path / "audit.jsonl")[0]["ts"].endswith("Z")


def test_the_directory_is_created_if_missing(tmp_path: Path) -> None:
    """logs/ is gitignored, so a fresh clone does not have it and the first denial must still be
    recorded rather than lost to a missing directory."""
    log = AuditLog(tmp_path / "logs" / "audit.jsonl")

    log.record(make_record())

    assert (tmp_path / "logs" / "audit.jsonl").exists()
