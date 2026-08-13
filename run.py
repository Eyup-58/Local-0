"""Starts Local Zero: one command, and Ctrl+C stops everything it started.

    uv run python run.py

Two processes, not three. The brain serves the built UI from the same origin as its WebSocket, so
the front end is a set of static assets rather than a server of its own - which is also what keeps
the port in one place instead of two (see BIND_PORT in local_zero_brain/ws/server.py).

**Uvicorn runs in this process, deliberately.** ``uv run uvicorn`` puts a launcher between the
caller and the process that holds the port, and killing the launcher leaves the brain running -
measured during M6 and worked around by ``kill_tree`` in bench/fault_injection.py. Running the app
here means Ctrl+C reaches the server that is listening, and the ``finally`` below is what stops the
sidecar.

Every process here runs asInvoker. Nothing elevates, and the sidecar still refuses to start if
something else elevated it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent

# The brain is an application, not an installed package - pyproject.toml says so, and puts `brain`
# on the path for pytest only. This is the same line for the one other entry point.
sys.path.insert(0, str(REPOSITORY_ROOT / "brain"))

SIDECAR_EXE = (
    REPOSITORY_ROOT
    / "system"
    / "LocalZero.System"
    / "bin"
    / "Debug"
    / "net10.0-windows"
    / "LocalZero.System.exe"
)

#: The sidecar's pipe. Its existence means an instance is already serving - see already_running().
SIDECAR_PIPE = Path(r"\\.\pipe") / "LocalZero.System.v1"

#: How long the sidecar gets to fail. A missing .NET runtime exits immediately; this is generous.
SIDECAR_START_GRACE_S = 2.0
SIDECAR_STOP_TIMEOUT_S = 10.0
#: The model server either answers a tag listing at once or is not running.
OLLAMA_TIMEOUT_S = 2.0
#: Long enough for uvicorn to be listening, short enough that nobody notices the wait.
BROWSER_DELAY_S = 1.5


def require_sidecar() -> None:
    """A missing build names the command that produces it. Fatal: there is no telemetry without it."""
    if SIDECAR_EXE.exists():
        return

    print(f"The system sidecar is not built. Expected:\n  {SIDECAR_EXE}", file=sys.stderr)
    print(
        "Build it with:\n"
        "  dotnet build system/LocalZero.System/LocalZero.System.csproj\n"
        "This needs the .NET SDK 10.0 (https://dotnet.microsoft.com/download).",
        file=sys.stderr,
    )
    raise SystemExit(2)


def is_already_running() -> bool:
    """True when the sidecar's pipe exists, whoever is holding it.

    Listing the pipe directory rather than asking the path whether it exists: ``Path.exists()`` calls
    stat, and stat on a pipe whose instances are all busy raises WinError 231 instead of answering
    - measured 2026-08-13, which is how this line stopped being a one-liner.
    """
    return SIDECAR_PIPE.name in os.listdir(SIDECAR_PIPE.parent)


def refuse_a_second_instance() -> None:
    """A second instance would fail on the pipe. Measured: the sidecar throws IOException there.

    That stack trace is a true message about the wrong thing - the user's problem is that Local Zero
    is already running, and the pipe is the cheapest way to know it before anything is started.
    """
    if not is_already_running():
        return

    print(
        f"Local Zero is already running - {SIDECAR_PIPE} is taken.\n"
        f"Use the window that has it, or stop it first.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def check_ollama() -> None:
    """Reports the local model server and its models. Not fatal - the brain degrades honestly.

    A degrade that is announced is what M7's prerequisite criterion asks for; one that is silent is
    what it forbids. Cloud mode and the telemetry view both work with nothing installed here.
    """
    from local_zero_brain.llm.ollama import (
        DEFAULT_BASE_URL,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MODEL,
    )

    try:
        with urllib.request.urlopen(  # noqa: S310 - loopback, a constant, and not user input
            f"{DEFAULT_BASE_URL}/api/tags", timeout=OLLAMA_TIMEOUT_S
        ) as response:
            installed = {
                str(model.get("name", "")) for model in json.load(response).get("models", [])
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        print(
            f"  Ollama is not answering at {DEFAULT_BASE_URL} ({error}).\n"
            "  Local mode will have no model. Install it from https://ollama.com and start it,\n"
            f"  then: ollama pull {DEFAULT_MODEL} && ollama pull {DEFAULT_EMBEDDING_MODEL}"
        )
        return

    missing = [
        wanted
        for wanted in (DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL)
        if not any(name == wanted or name.startswith(f"{wanted}:") for name in installed)
    ]
    for model in missing:
        print(f"  {model} is not pulled. Run: ollama pull {model}")

    if not missing:
        print(f"  Ollama: {DEFAULT_MODEL} and {DEFAULT_EMBEDDING_MODEL} present.")


def start_sidecar() -> subprocess.Popen[str]:
    """Launches the sidecar and gives it long enough to fail out loud.

    No shell, no command line built by concatenation: one argument in a list, per red line 3.
    """
    process = subprocess.Popen(
        [str(SIDECAR_EXE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    try:
        process.wait(timeout=SIDECAR_START_GRACE_S)
    except subprocess.TimeoutExpired:
        print("  Sidecar: running.")
        return process

    stderr = (process.stderr.read() if process.stderr else "") or "(it printed nothing)"
    print(
        f"The sidecar exited immediately with code {process.returncode}. It said:\n{stderr.strip()}",
        file=sys.stderr,
    )
    print(
        "If that names a missing runtime, install the .NET 10 desktop runtime from\n"
        "https://dotnet.microsoft.com/download and try again.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def stop_sidecar(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=SIDECAR_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    require_sidecar()
    refuse_a_second_instance()

    # ASCII only in everything this prints: the Windows console encoding is not UTF-8 by default,
    # and a launcher that dies of a UnicodeEncodeError on somebody else's code page would fail at
    # exactly the thing M7 exists to deliver.
    print("Local Zero - checking what is installed:")
    check_ollama()

    # Importing the server builds the app, which is also what reports whether the UI was built.
    # One place says it, and that place is the thing that would have served it.
    from local_zero_brain.ws.server import BIND_HOST, BIND_PORT, app

    import uvicorn

    sidecar = start_sidecar()
    url = f"http://{BIND_HOST}:{BIND_PORT}"
    print(f"\nLocal Zero is at {url} - Ctrl+C stops it.\n")

    opener = threading.Timer(BROWSER_DELAY_S, lambda: webbrowser.open(url))
    opener.daemon = True
    opener.start()

    try:
        uvicorn.run(app, host=BIND_HOST, port=BIND_PORT, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        opener.cancel()
        stop_sidecar(sidecar)
        print("stopped.")

    return 0


if __name__ == "__main__":
    if os.name != "nt":
        print("Local Zero is a Windows product.", file=sys.stderr)
        raise SystemExit(2)

    raise SystemExit(main())
