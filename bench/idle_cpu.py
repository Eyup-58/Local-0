"""P5 - idle CPU across all three processes.

Definition (docs/PERFORMANCE.md section 2): sum of CPU time across the three processes over a fixed
window, divided by wall time, with the UI visible and telemetry streaming.

P5 has no provisional budget on purpose. Setting a CPU budget before measuring the floor produces
either a figure that is trivially met or one that is unreachable for reasons the design cannot
influence. This script measures the floor; the budget is set from what it reports.

The trap named in the method is that P5 must be measured through the same English-counter path the
product uses, or the bench half-works on this machine exactly the way the product would. This
script sidesteps that differently and more directly: it reads CPU *time* per process from the OS
rather than sampling a utilization counter at all, so there is no counter path to get wrong.

Requires the stack to already be running.

Run:
    uv run python bench/idle_cpu.py
"""

from __future__ import annotations

import sys
import time

import psutil
from _harness import Report, duration_argument, find_processes, warn_if_shortened

SPECIFIED_DURATION_S = 300

#: A single logical processor fully busy for the whole window. The machine has 28, so 100% here
#: means one core, not the whole machine.
PERCENT_OF_ONE_CORE = 100.0


def main() -> int:
    parser = duration_argument(SPECIFIED_DURATION_S)
    parser.description = "Measures idle CPU across the three layers (budget P5)."
    arguments = parser.parse_args()

    report = Report(
        metric="P5-idle-cpu",
        script="bench/idle_cpu.py",
        method=(
            f"{arguments.duration}s window, CPU time read per process from the OS and divided by "
            f"wall time; reported as percent of one logical processor"
        ),
    )
    warn_if_shortened(report, arguments.duration, SPECIFIED_DURATION_S, "measurement window")

    processes = find_processes()
    if "system" not in processes or "brain" not in processes:
        print(f"Expected the sidecar and the brain to be running. Found: {sorted(processes)}", file=sys.stderr)
        return 2

    if "ui" not in processes:
        report.note("no vite process found; the UI's own cost is not included in this total")

    report.note(f"measured layers: {', '.join(sorted(processes))}")
    report.note(f"machine has {psutil.cpu_count()} logical processors")

    start_times = {}
    for name, process in processes.items():
        cpu = process.cpu_times()
        start_times[name] = cpu.user + cpu.system

    print(f"Measuring for {arguments.duration}s...")
    started = time.monotonic()
    time.sleep(arguments.duration)
    wall = time.monotonic() - started

    total = 0.0
    for name, process in processes.items():
        try:
            cpu = process.cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            report.note(f"{name} disappeared before the window closed; its share is not counted")
            continue

        consumed = (cpu.user + cpu.system) - start_times[name]
        share = consumed / wall * PERCENT_OF_ONE_CORE
        total += share

        # A layer that is doing work cannot consume no measurable CPU. Reporting a zero here would
        # be the same failure P4's method warns about - a suspiciously-perfect number accepted as
        # success - so it is called out rather than averaged in.
        if name in ("system", "brain") and consumed <= 0.0:
            report.note(
                f"{name} reported no measurable CPU over {wall:.0f}s. That layer processes a "
                f"sample every second, so this is far more likely to be the wrong process than an "
                f"exceptionally cheap one. Treat this run as suspect."
            )

        report.values[name] = {"cpu_seconds": round(consumed, 3), "percent_of_one_core": round(share, 3)}
        print(f"  {name}: {consumed:.2f}s CPU over {wall:.1f}s wall = {share:.3f}% of one core")

    report.values["total"] = {"percent_of_one_core": round(total, 3), "wall_seconds": round(wall, 1)}
    print(f"\nP5 total idle CPU: {total:.3f}% of one logical processor")

    report.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
