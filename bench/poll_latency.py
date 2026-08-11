"""P3 - telemetry poll duration.

Definition (docs/PERFORMANCE.md section 2): wall time inside the sidecar from the start of a sensor
sweep to a complete telemetry.sample ready to write. Excludes the pipe write and everything
downstream, which is why the number comes from the sidecar's own clock rather than from timing
observed at the far end.

Method: at least 600 consecutive samples, reporting p50 / p95 / p99 / max.

The trap this script exists to avoid: the first PDH reads after opening a counter query return no
usable value, because a rate counter needs two collections before it has one. Including them would
report an implausibly good p95 built partly out of sweeps that measured nothing. The first five are
discarded, and the output says so.

Run:
    uv run python bench/poll_latency.py
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from _harness import Distribution, PipeDrain, Report, duration_argument, start_sidecar, warn_if_shortened

SPECIFIED_SAMPLES = 600
DISCARDED_WARMUP_SAMPLES = 5

SWEEP_PREFIX = "bench sweep_ms="


def main() -> int:
    parser = duration_argument(SPECIFIED_SAMPLES)
    parser.description = "Measures sidecar sweep duration (budget P3)."
    arguments = parser.parse_args()
    wanted = arguments.duration

    report = Report(
        metric="P3-poll-latency",
        script="bench/poll_latency.py",
        method=(
            f"{wanted} consecutive sweeps at the sidecar's own tick, first "
            f"{DISCARDED_WARMUP_SAMPLES} discarded as PDH warm-up"
        ),
    )
    warn_if_shortened(report, wanted, SPECIFIED_SAMPLES, "sample count")

    print(f"Collecting {wanted + DISCARDED_WARMUP_SAMPLES} sweeps at 1 Hz. This takes about "
          f"{(wanted + DISCARDED_WARMUP_SAMPLES) // 60} minutes.")
    print("A drain attaches to the pipe so the sidecar is in its real streaming state. It does not")
    print("sweep with nobody connected, and P3 stops at the point a sample is ready to write.\n")

    durations: list[float] = []
    sidecar = start_sidecar(bench_mode=True)
    drain = PipeDrain()

    try:
        drain.__enter__()
        if not drain.connected.wait(timeout=15):
            print("Could not attach to the sidecar's pipe.", file=sys.stderr)
            return 1

        assert sidecar.stderr is not None

        # readline rather than iterating the file: iteration uses a read-ahead buffer, so lines
        # arriving once a second do not surface until the buffer happens to fill. Measured - the
        # first version of this script produced nothing and looked like a hung sidecar.
        for line in iter(sidecar.stderr.readline, ""):
            index = line.find(SWEEP_PREFIX)
            if index < 0:
                continue

            durations.append(float(line[index + len(SWEEP_PREFIX):].strip()))
            collected = len(durations)

            if collected % 60 == 0:
                print(f"  {collected} sweeps")

            if collected >= wanted + DISCARDED_WARMUP_SAMPLES:
                break
    finally:
        drain.__exit__()
        sidecar.terminate()
        sidecar.wait(timeout=10)

    if len(durations) <= DISCARDED_WARMUP_SAMPLES:
        print("The sidecar produced no usable sweeps.", file=sys.stderr)
        return 1

    warmup = durations[:DISCARDED_WARMUP_SAMPLES]
    measured = durations[DISCARDED_WARMUP_SAMPLES:]

    report.note(
        f"discarded the first {DISCARDED_WARMUP_SAMPLES} sweeps as PDH warm-up: "
        + ", ".join(f"{value:.2f}ms" for value in warmup)
    )

    distribution = Distribution.of(measured)
    report.values = {"sweep_ms": asdict(distribution)}

    print(f"\nP3 sweep duration: {distribution.render('ms')}")
    report.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
