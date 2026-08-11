"""P1 and P2 - idle working set of the sidecar and the brain.

Definition (docs/PERFORMANCE.md section 2): working set of each process after startup has settled,
with the UI connected and telemetry streaming, and no user interaction.

Method: wait 60 s for settling, then sample once per second for 300 s. Report median and max, and
**report the max against the budget** - a process that transiently doubles is a process that will
be observed doing so.

The trap: Windows working set is not a clean allocation measure. It responds to system memory
pressure and can shrink without the process freeing anything, so a run taken while the machine is
under pressure measures the pressure. This script records memory load throughout and discards the
run if it moved materially.

Requires the stack to already be running. Start it with bench/run_stack.py.

Run:
    uv run python bench/idle_rss.py
"""

from __future__ import annotations

import statistics
import sys

import psutil
from _harness import Report, duration_argument, find_processes, memory_load_percent, sample_clock, warn_if_shortened

SPECIFIED_DURATION_S = 300
SPECIFIED_SETTLE_S = 60
SAMPLE_INTERVAL_S = 1.0

#: How far system memory load may move before the run stops describing the processes and starts
#: describing the machine.
MEMORY_LOAD_TOLERANCE = 10

BYTES_PER_MIB = 1024 * 1024


def main() -> int:
    parser = duration_argument(SPECIFIED_DURATION_S, SPECIFIED_SETTLE_S)
    parser.description = "Measures idle working set (budgets P1 and P2)."
    arguments = parser.parse_args()

    report = Report(
        metric="P1P2-idle-rss",
        script="bench/idle_rss.py",
        method=f"{arguments.settle}s settle, then {arguments.duration}s at 1 Hz; max reported against the budget",
    )
    warn_if_shortened(report, arguments.duration, SPECIFIED_DURATION_S, "measurement window")
    warn_if_shortened(report, arguments.settle, SPECIFIED_SETTLE_S, "settling period")

    processes = find_processes()
    if "system" not in processes or "brain" not in processes:
        print(f"Expected the sidecar and the brain to be running. Found: {sorted(processes)}", file=sys.stderr)
        print("Start them with: uv run python bench/run_stack.py", file=sys.stderr)
        return 2

    report.note(f"measured layers: {', '.join(sorted(processes))}")

    print(f"Settling for {arguments.settle}s...")
    for _ in sample_clock(arguments.settle, SAMPLE_INTERVAL_S):
        pass

    load_start = memory_load_percent()
    samples: dict[str, list[float]] = {name: [] for name in processes}
    loads: list[int] = []

    print(f"Sampling for {arguments.duration}s...")
    for elapsed in sample_clock(arguments.duration, SAMPLE_INTERVAL_S):
        loads.append(memory_load_percent())

        for name, process in processes.items():
            try:
                samples[name].append(process.memory_info().rss / BYTES_PER_MIB)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                report.note(f"{name} disappeared {elapsed:.0f}s into the run")

        if int(elapsed) % 60 == 0 and int(elapsed) > 0 and len(loads) % 60 == 1:
            print(f"  {int(elapsed)}s")

    load_end = memory_load_percent()
    load_swing = max(loads) - min(loads)
    report.note(f"system memory load {load_start}% -> {load_end}%, swing {load_swing} points")

    if load_swing > MEMORY_LOAD_TOLERANCE:
        report.discard(
            f"system memory load moved {load_swing} points during the run, over the "
            f"{MEMORY_LOAD_TOLERANCE} point tolerance. Windows working set responds to pressure, "
            f"so this run measures the machine rather than the processes."
        )

    for name, values in samples.items():
        if not values:
            continue

        report.values[name] = {
            "median_mib": round(statistics.median(values), 1),
            "max_mib": round(max(values), 1),
            "samples": len(values),
        }
        print(f"\n{name}: median {statistics.median(values):.1f} MiB, max {max(values):.1f} MiB "
              f"(n={len(values)})")

    report.write()
    return 1 if report.discarded else 0


if __name__ == "__main__":
    raise SystemExit(main())
