"""The launcher's one piece of real logic: what it starts, it stops.

Everything else in run.py is straight-line reporting. This is the part that leaves something behind
if it is wrong, and a sidecar that outlives its launcher is exactly the failure M6's fault injection
had to work around in the bench harness.

Skipped rather than failed when the sidecar is not built or an instance already holds the pipe:
those are states of the machine, not of the code under test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_launcher():
    """run.py sits at the repository root and is not importable as a package member."""
    spec = importlib.util.spec_from_file_location("local_zero_run", REPOSITORY_ROOT / "run.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()

def _why_the_sidecar_cases_cannot_run() -> str:
    if sys.platform != "win32":
        return "Local Zero is a Windows product"
    if not launcher.SIDECAR_EXE.exists():
        return "the sidecar is not built; run dotnet build"
    if launcher.is_already_running():
        return "an instance is already running and holds the pipe"
    return ""


_BLOCKER = _why_the_sidecar_cases_cannot_run()

#: For the two cases that start a real sidecar. The rest of this file runs anywhere.
needs_sidecar = pytest.mark.skipif(bool(_BLOCKER), reason=_BLOCKER or "runnable")


def _urlopen_that_fails(*_: object, **__: object):
    raise OSError("connection refused")


def test_a_missing_ollama_names_it_and_the_pulls_without_stopping_the_start(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The prerequisite criterion asks for a named degrade, not a prevented one."""
    monkeypatch.setattr(launcher.urllib.request, "urlopen", _urlopen_that_fails)

    launcher.check_ollama()

    printed = capsys.readouterr().out
    assert "11434" in printed
    assert "ollama pull qwen2.5:14b" in printed
    assert "ollama pull nomic-embed-text" in printed


def test_a_model_that_is_not_pulled_is_named_with_the_command_that_pulls_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A server that is up with the wrong models is a different fact from one that is down."""
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *_, **__: _FakeResponse('{"models": [{"name": "qwen2.5:14b"}]}'),
    )

    launcher.check_ollama()

    printed = capsys.readouterr().out
    assert "ollama pull nomic-embed-text" in printed
    assert "qwen2.5:14b" not in printed.replace("ollama pull nomic-embed-text", "")


class _FakeResponse:
    """Just enough of an HTTP response for json.load and a `with` block."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self, *_: object) -> bytes:
        payload, self._body = self._body, b""
        return payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@needs_sidecar
def test_stopping_the_launcher_stops_the_sidecar_it_started() -> None:
    sidecar = launcher.start_sidecar()
    assert sidecar.poll() is None, "the sidecar did not stay up long enough to be stopped"

    launcher.stop_sidecar(sidecar)

    assert sidecar.poll() is not None, "the sidecar outlived the launcher that started it"


@needs_sidecar
def test_stopping_a_sidecar_that_already_exited_is_not_an_error() -> None:
    """Ctrl+C after a crash must not raise on the way out and hide what actually happened."""
    sidecar = launcher.start_sidecar()
    launcher.stop_sidecar(sidecar)

    launcher.stop_sidecar(sidecar)
