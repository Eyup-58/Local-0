"""P4 - WebSocket message latency.

Definition (docs/PERFORMANCE.md section 2): from ``sampled_at`` in the payload to the moment the
consumer's handler receives the frame.

**The trap, and what this script can and cannot claim.** The method warns that this metric is only
as good as the clock, because in the browser ``performance.now()`` and a .NET timestamp are two
different time origins, and comparing them without establishing an offset produces a negative or
suspiciously-zero latency that looks like success.

This harness is not the browser. It is a Python client on the same machine reading the same system
clock the sidecar stamps ``sampled_at`` from, so there is no cross-origin offset to establish and
none is faked. What it measures is therefore honest but narrower than the budget's wording:

    sidecar sweep completion -> pipe -> brain -> WebSocket -> a local Python handler

**The browser leg remains not measured.** Whatever the UI adds on top of this is unknown until
something instruments it, and PERFORMANCE.md section 5 records it that way rather than quoting this
number as if it covered the whole path.

Requires the stack to already be running.

Run:
    uv run python bench/ws_latency.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import websockets
from _harness import BRAIN_WS_URL, Distribution, Report, duration_argument, warn_if_shortened

SPECIFIED_SAMPLES = 600

#: A latency at or below zero means the clocks are not comparable after all, whatever the method
#: assumed. Counted and reported rather than quietly averaged away.
IMPLAUSIBLE_MS = 0.0


def client_hello() -> str:
    now = datetime.now(UTC)
    return json.dumps(
        {
            "v": 1,
            "id": str(uuid4()),
            "ts": f"{now.strftime('%Y-%m-%dT%H:%M:%S.')}{now.microsecond // 1000:03d}Z",
            "type": "client.hello",
            "payload": {"component": "ui", "app_version": "0.1.0"},
        }
    )


async def collect(wanted: int, report: Report) -> list[float]:
    latencies: list[float] = []
    implausible = 0

    async with websockets.connect(BRAIN_WS_URL) as socket:
        await socket.send(client_hello())

        while len(latencies) < wanted:
            frame = json.loads(await asyncio.wait_for(socket.recv(), 30))
            received = datetime.now(UTC)

            if frame["type"] != "telemetry.sample":
                continue

            sampled_at = datetime.strptime(frame["payload"]["sampled_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=UTC
            )
            latency_ms = (received - sampled_at).total_seconds() * 1000

            if latency_ms <= IMPLAUSIBLE_MS:
                implausible += 1
                continue

            latencies.append(latency_ms)
            if len(latencies) % 60 == 0:
                print(f"  {len(latencies)} samples")

    if implausible:
        report.note(
            f"{implausible} samples had a latency at or below zero and were excluded. A "
            f"non-positive latency means the two timestamps are not comparable, and averaging it "
            f"in would flatter the result."
        )

    return latencies


def main() -> int:
    parser = duration_argument(SPECIFIED_SAMPLES)
    parser.description = "Measures sidecar-to-local-consumer latency (budget P4)."
    arguments = parser.parse_args()

    report = Report(
        metric="P4-ws-latency",
        script="bench/ws_latency.py",
        method=(
            f"{arguments.duration} telemetry frames; sampled_at to receipt in a local Python "
            f"client sharing the system clock. Excludes the browser leg."
        ),
    )
    warn_if_shortened(report, arguments.duration, SPECIFIED_SAMPLES, "sample count")
    report.note(
        "measures sidecar sweep completion through to a local Python handler. The browser's own "
        "contribution is NOT measured and must not be inferred from this number."
    )

    print(f"Collecting {arguments.duration} frames at 1 Hz from {BRAIN_WS_URL}.")

    try:
        latencies = asyncio.run(collect(arguments.duration, report))
    except (OSError, asyncio.TimeoutError) as error:
        print(f"Could not collect from the brain: {error}", file=sys.stderr)
        print("Start the stack with: uv run python bench/run_stack.py", file=sys.stderr)
        return 2

    if not latencies:
        print("No usable samples.", file=sys.stderr)
        return 1

    distribution = Distribution.of(latencies)
    report.values = {"latency_ms": asdict(distribution)}

    print(f"\nP4 latency (excluding the browser): {distribution.render('ms')}")
    report.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
