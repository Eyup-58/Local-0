"""Shared plumbing for the bench scripts.

The rule this directory exists to serve, from docs/PERFORMANCE.md: no performance claim without a
script here that produced it, and a number in that document names the script and the date it was
taken. These helpers keep the four scripts honest about the same things - which process is which,
how percentiles are computed, and whether the machine was in a state where the run counts.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPOSITORY_ROOT / "bench" / "results"

SIDECAR_EXE = (
    REPOSITORY_ROOT / "system" / "LocalZero.System" / "bin" / "Debug" / "net10.0-windows" / "LocalZero.System.exe"
)

BRAIN_PORT = 8765
BRAIN_WS_URL = f"ws://127.0.0.1:{BRAIN_PORT}/ws"


@dataclass(frozen=True, slots=True)
class Distribution:
    """A measured distribution. p95 rather than the mean, deliberately.

    An average hides the stall. A sampler that completes in 3 ms most ticks and 400 ms
    occasionally has a fine average and a visibly janky readout, so the tail is what gets a budget.
    """

    count: int
    p50: float
    p95: float
    p99: float
    maximum: float
    minimum: float

    @staticmethod
    def of(values: Sequence[float]) -> Distribution:
        ordered = sorted(values)
        return Distribution(
            count=len(ordered),
            p50=_percentile(ordered, 0.50),
            p95=_percentile(ordered, 0.95),
            p99=_percentile(ordered, 0.99),
            maximum=ordered[-1],
            minimum=ordered[0],
        )

    def render(self, unit: str) -> str:
        return (
            f"n={self.count}  p50={self.p50:.2f}{unit}  p95={self.p95:.2f}{unit}  "
            f"p99={self.p99:.2f}{unit}  max={self.maximum:.2f}{unit}"
        )


def _percentile(ordered: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile. No interpolation, so every reported number is one that occurred."""
    if not ordered:
        raise ValueError("cannot take a percentile of no samples")

    rank = max(1, min(len(ordered), round(quantile * len(ordered))))
    return ordered[rank - 1]


@dataclass
class Report:
    """One bench run, written to bench/results/ so a number in PERFORMANCE.md has provenance."""

    metric: str
    script: str
    taken_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    method: str = ""
    values: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    discarded: bool = False
    discard_reason: str | None = None

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note: {text}")

    def discard(self, reason: str) -> None:
        """A run taken under conditions the method excludes is discarded, not reported.

        PERFORMANCE.md is explicit that a run under memory pressure is discarded rather than
        reported. Quietly keeping it would be the difference between a measurement and a number.
        """
        self.discarded = True
        self.discard_reason = reason
        print(f"  DISCARDED: {reason}")

    def write(self) -> Path:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"{self.metric}-{stamp}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        print(f"\nwritten: {path.relative_to(REPOSITORY_ROOT).as_posix()}")
        return path


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_load_percent() -> int:
    """System-wide memory pressure, 0-100.

    Windows working set responds to system memory pressure and can shrink without the process
    freeing anything, so an RSS run taken while the machine is under pressure measures the
    pressure rather than the process.
    """
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(status.dwMemoryLoad)


def find_processes() -> dict[str, psutil.Process]:
    """Locates the three layers by what they are running, not by a pid handed in.

    Returns only the ones found, so a script can say which layer it could not measure instead of
    reporting a total that quietly omits one.
    """
    found: dict[str, psutil.Process] = {}

    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (process.info["name"] or "").lower()
            cmdline = " ".join(process.info["cmdline"] or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if name == "localzero.system.exe":
            found["system"] = process

        # The interpreter itself, not the launcher. `uv run uvicorn ...` produces a uv.exe parent
        # whose command line matches just as well as its python.exe child's, and it sits idle
        # waiting for that child. Measured: matching on the command line alone selected the wrapper
        # and reported the brain as 5.4 MiB using 0.000s of CPU over five minutes, which is not
        # something a process validating a sample per second can do.
        elif name in ("python.exe", "pythonw.exe") and "local_zero_brain" in cmdline:
            found["brain"] = process

        # There is deliberately no "ui" entry. Until M7 the UI was a Vite process and could be
        # measured; the brain now serves the built assets and the UI is a tab in whichever browser
        # the user has open. Matching on that would report the browser's cost - tabs, extensions and
        # all - as Local Zero's, which is a worse answer than reporting that it is not measured.
        # The scripts that used to include it say so in their notes.

    return found


def sample_clock(duration_s: float, interval_s: float) -> Iterable[float]:
    """Yields elapsed seconds on a fixed cadence, without drifting.

    Sleeping a fixed interval accumulates the time each iteration's work took; this schedules
    against a start time so a 300 second run is 300 seconds.
    """
    started = time.monotonic()
    tick = 0

    while True:
        elapsed = time.monotonic() - started
        if elapsed >= duration_s:
            return

        yield elapsed
        tick += 1

        target = started + tick * interval_s
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def require_built_sidecar() -> None:
    if not SIDECAR_EXE.exists():
        print(f"The sidecar is not built. Expected {SIDECAR_EXE}", file=sys.stderr)
        print("Run: dotnet build system/LocalZero.System/LocalZero.System.csproj", file=sys.stderr)
        raise SystemExit(2)


def start_sidecar(*, bench_mode: bool) -> subprocess.Popen[str]:
    """Starts the sidecar with its stderr captured.

    bench_mode turns on the per-tick sweep duration line that budget P3 is measured from. It is off
    in normal operation - see LocalZero.System/Diagnostics/BenchMode.cs.
    """
    require_built_sidecar()

    environment = {"LOCALZERO_BENCH": "1"} if bench_mode else {}
    import os

    return subprocess.Popen(
        [str(SIDECAR_EXE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **environment},
    )


def start_brain() -> subprocess.Popen[bytes]:
    """Starts the brain the way a user would, through uv.

    Lives here rather than in run_stack.py because fault_injection.py starts and kills its own
    stack: two callers, one definition, and the fault harness must be killing the same thing the
    measuring harness starts.

    **Note for anything that kills this.** ``uv run`` launches uvicorn as a child, so the returned
    Popen is a launcher rather than the process holding the socket. Killing it alone leaves the
    brain running and the port taken - see ``kill_tree`` in fault_injection.py.
    """
    return subprocess.Popen(
        [
            "uv", "run", "uvicorn",
            "--app-dir", "brain",
            "local_zero_brain.ws.server:app",
            "--host", "127.0.0.1",
            "--port", str(BRAIN_PORT),
            "--log-level", "warning",
        ],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class PipeDrain:
    """Attaches to the sidecar's pipe and discards everything it sends.

    The sidecar only sweeps while a client is connected - it does not tick into the void, which is
    the right behaviour and is why P5's idle cost is what it is. So a bench that wants sweeps has
    to be a consumer, and this is the smallest honest one: it puts the sidecar in exactly the state
    a real brain would, without the brain's validation costs landing in the measurement.
    """

    def __init__(self, pipe_name: str = "LocalZero.System.v1") -> None:
        self._path = rf"\\.\pipe\{pipe_name}"
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-pipe-drain", daemon=True)
        self.connected = threading.Event()

    def __enter__(self) -> PipeDrain:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stopping.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        import pywintypes
        import win32file

        handle = None
        while not self._stopping.is_set() and handle is None:
            try:
                handle = win32file.CreateFile(
                    self._path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
            except pywintypes.error:
                self._stopping.wait(0.2)

        if handle is None:
            return

        self.connected.set()
        try:
            while not self._stopping.is_set():
                win32file.ReadFile(handle, 64 * 1024)
        except pywintypes.error:
            pass
        finally:
            win32file.CloseHandle(handle)


def duration_argument(default_s: int, settle_s: int = 0) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--duration",
        type=int,
        default=default_s,
        help=f"measurement window in seconds (method specifies {default_s})",
    )
    if settle_s:
        parser.add_argument(
            "--settle",
            type=int,
            default=settle_s,
            help=f"settling period before measurement begins (method specifies {settle_s})",
        )
    return parser


def warn_if_shortened(report: Report, actual: int, specified: int, label: str) -> None:
    """A run shorter than the method is recorded as such rather than reported as if it were not."""
    if actual < specified:
        report.note(
            f"{label} was {actual}s, short of the {specified}s the method specifies. "
            f"This run is indicative only and must not be quoted as a conforming measurement."
        )
