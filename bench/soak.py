"""M6 - long-run profile: does an hour of real use grow anything without bound?

`docs/ROADMAP.md` M6 asks for "no unbounded memory growth over an extended session". P1 and P2
already measured the idle working set at M1, so an idle hour would mostly re-measure what is known.
The paths that could actually leak are the ones M4 and M4.5 added - the planner, the reader, the
embedding index, the sqlite store and the capability results - and none of them runs unless somebody
asks for something. So this drives a real workload over the real socket while it samples.

**The verdict is a slope, not a delta.** `fault_injection.py` learned this the expensive way: two
endpoints cannot tell a plateau from a slope, and a collector pass landing just before the final read
hides a window in which the process really did grow. Every layer is reported as a least-squares
MiB/hour with its quartile medians and its peak, so the shape is the evidence and the threshold is
only a guard.

**The verdict is on private commit, not working set, and that was learned the hard way.** The first
conforming-length run was discarded: 43 minutes in, Windows trimmed all three working sets in the
same second - brain 61.8 -> 7.2 MiB, the UI 68.4 -> 1.5, the sidecar 63.8 -> 7.5 - and then they
faulted back in. Three processes do not free 85 % of their memory simultaneously; the machine
reclaimed it. Working set answers "how much is resident right now", which is a question about system
pressure. **Private bytes** is what the process asked for and has not given back, it is not trimmed,
and it is the only one of the two that can answer whether something grows without bound. RSS is still
sampled and reported - it is what P1 and P2 are written against - but it does not decide the verdict.

**Handles are sampled too.** On Windows a handle leak is the other shape of unbounded growth, it
costs nothing to read, and it would not appear in RSS until long after the session this bench is
meant to represent.

Requires the stack to already be running, and Ollama up - a run whose turns never get answered
measures an idle brain and says so rather than reporting the flat line as a pass.

Run:
    uv run python bench/run_stack.py     # in one terminal
    uv run python bench/soak.py          # in another
    uv run python bench/soak.py --self-check
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import psutil
import websockets
from _harness import (
    BRAIN_WS_URL,
    Report,
    duration_argument,
    find_processes,
    memory_load_percent,
    sample_clock,
    warn_if_shortened,
)

SPECIFIED_DURATION_S = 3600
SAMPLE_INTERVAL_S = 5.0

#: Sampling starts warm, and "warm" here means more than the sidecar's PDH and ADLX caches.
#:
#: Measured on a 200 s trial run: system memory load moved 18 % -> 31 % and the run was correctly
#: discarded, because loading gemma4:26b - 17 GB against 16 GB of VRAM - is itself a 13 point swing.
#: A soak that drives an LLM has to warm the model before it starts sampling, or the tolerance
#: rejects every conforming run and the machine's load is what got measured. The same trial showed
#: the sidecar climbing 47.7 -> 56.3 MiB over its first four minutes, which is the counter warm-up
#: P1's 60 s settle exists for.
SPECIFIED_SETTLE_S = 180

#: How long past the settle window the first answered turn is waited for before giving up on a warm
#: start. A cold gemma4 load is slow and variable; this is generous rather than tight, and a run that
#: uses it says so.
WARMUP_GRACE_S = 180.0

#: How often a turn is asked for. A reader answer over one small chunk measured 55.6 s on this
#: machine (gemma4:26b spilling to CPU), so this is roughly one turn per answered turn plus slack -
#: enough to exercise the path about thirty times in an hour without queueing requests behind each
#: other and measuring the queue instead.
TURN_INTERVAL_S = 120.0

#: A full vault rescan, six times in an hour. The incremental path is what a long session actually
#: runs; if it re-embeds what it already has, this is where that shows up as a climb.
REINDEX_INTERVAL_S = 600.0

#: Growth that would matter, in **private commit**. At 10 MiB/hour a session left open for a working
#: day adds 80 MiB - more than the whole idle budget of either layer (P1 and P2 are both < 80 MiB),
#: so this is the rate at which "leave it running" stops being free. A per-turn leak climbs far
#: faster than this; allocator noise over thirty turns does not come close.
GROWTH_SLOPE_MB_PER_HOUR = 10.0

#: A drop this steep between two samples five seconds apart is the OS trimming a working set, not a
#: process freeing memory. Counted so the RSS series carries an explanation for its own cliffs
#: instead of leaving a reader to guess at one.
TRIM_DROP_FRACTION = 0.5

#: One handle leaked per sample would be 720/hour at this cadence, and one per turn about 30. This
#: sits above the second and an order of magnitude below the first: it catches a leak tied to work
#: being done without failing on a pool that sizes itself once and stays there.
HANDLE_SLOPE_PER_HOUR = 100.0

#: Same constant as idle_rss.py, weaker consequence: there it discards the run, here it annotates
#: it. See the note where it is used - the verdict metric is not the one pressure distorts.
MEMORY_LOAD_TOLERANCE = 10

BYTES_PER_MIB = 1024 * 1024

#: Read-only prompts, deliberately. A destructive one would sit in the approval queue with nobody to
#: answer it, and the soak would spend its hour measuring a brain waiting for a click. Two reach a
#: capability, one reaches memory recall, one is a question no capability fits - between them they
#: cover the planner's three outcomes.
PROMPTS = (
    "Which processes are using the most memory right now?",
    "What games are installed on this machine?",
    "What do my notes say about this project?",
    "How does a pipe differ from a socket?",
)


def frame(message_type: str, payload: dict) -> str:
    """A contract frame. Same shape as the one in fault_injection.py, which the brain accepts."""
    now = datetime.now(UTC)
    return json.dumps(
        {
            "v": 1,
            "id": str(uuid4()),
            "ts": f"{now.strftime('%Y-%m-%dT%H:%M:%S.')}{now.microsecond // 1000:03d}Z",
            "type": message_type,
            "payload": payload,
        }
    )


def slope_per_hour(elapsed_s: Sequence[float], values: Sequence[float]) -> float:
    """Least-squares slope in units per hour.

    Ordinary least squares rather than a line through the first and last points: one collector pass
    at either end would otherwise decide the verdict for the whole run.
    """
    count = len(values)
    if count < 2:
        return 0.0

    mean_x = statistics.fmean(elapsed_s)
    mean_y = statistics.fmean(values)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(elapsed_s, values, strict=True))
    variance = sum((x - mean_x) ** 2 for x in elapsed_s)
    if variance == 0:
        return 0.0

    return covariance / variance * 3600.0


def quartile_medians(values: Sequence[float]) -> tuple[float, float]:
    """Median of the first quarter and of the last quarter, as a second view of the same question."""
    quarter = max(1, len(values) // 4)
    return statistics.median(values[:quarter]), statistics.median(values[-quarter:])


@dataclass
class Workload:
    """What the driver actually managed to do, so the report can say what the soak measured."""

    turns_sent: int = 0
    turns_answered: int = 0
    reindexes_sent: int = 0
    telemetry_frames: int = 0
    tool_logs: int = 0
    capability_results: int = 0
    errors: list[str] = field(default_factory=list)
    reconnects: int = 0
    embeddings_available: bool | None = None
    #: Set while a turn is outstanding, cleared when the brain leaves `thinking`.
    _pending: bool = False


def counters(workload: Workload) -> dict[str, int]:
    """The countable part of the workload, so a settle-phase snapshot can be subtracted from it.

    The warm-up turn is real work the brain did, and leaving it in would credit the measured window
    with a turn taken before sampling began.
    """
    return {
        "turns_sent": workload.turns_sent,
        "turns_answered": workload.turns_answered,
        "reindexes_sent": workload.reindexes_sent,
        "telemetry_frames": workload.telemetry_frames,
        "tool_logs": workload.tool_logs,
        "capability_results": workload.capability_results,
        "reconnects": workload.reconnects,
        "error_count": len(workload.errors),
    }


def settle(workload: Workload, seconds: int, report: Report) -> None:
    """Waits out the settle window, and then for the model to be in memory."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)

    if workload.turns_answered:
        return

    print(f"  no turn answered yet; waiting up to {WARMUP_GRACE_S:.0f}s more for a warm model")
    grace = time.monotonic() + WARMUP_GRACE_S
    while time.monotonic() < grace and workload.turns_answered == 0:
        time.sleep(1.0)

    if workload.turns_answered == 0:
        report.note(
            f"no turn was answered during the {seconds}s settle or the {WARMUP_GRACE_S:.0f}s grace "
            f"after it. Sampling starts anyway, and the model may load during the measured window - "
            f"which is exactly the memory-load swing the tolerance discards a run for."
        )


async def _drive_once(workload: Workload, stop: threading.Event) -> None:
    """One connection's worth of driving. Returns when the socket dies or the soak ends."""
    next_turn = time.monotonic() + 10.0
    next_reindex = time.monotonic() + REINDEX_INTERVAL_S

    async with websockets.connect(BRAIN_WS_URL) as socket:
        await socket.send(frame("client.hello", {"component": "ui", "app_version": "0.1.0"}))

        while not stop.is_set():
            try:
                received = json.loads(await asyncio.wait_for(socket.recv(), 1.0))
            except (TimeoutError, asyncio.TimeoutError):
                received = None

            if received is not None:
                _record(workload, received)

            now = time.monotonic()
            if now >= next_turn:
                prompt = PROMPTS[workload.turns_sent % len(PROMPTS)]
                await socket.send(frame("turn.request", {"text": prompt}))
                workload.turns_sent += 1
                workload._pending = True
                next_turn = now + TURN_INTERVAL_S

            if now >= next_reindex:
                await socket.send(frame("memory.reindex", {}))
                workload.reindexes_sent += 1
                next_reindex = now + REINDEX_INTERVAL_S


def _record(workload: Workload, received: dict) -> None:
    kind = received.get("type")
    payload = received.get("payload") or {}

    if kind == "telemetry.sample":
        workload.telemetry_frames += 1
    elif kind == "tool.log":
        workload.tool_logs += 1
    elif kind == "capability.result":
        workload.capability_results += 1
    elif kind == "memory.status":
        workload.embeddings_available = payload.get("embeddings_available")
    elif kind == "error":
        # Kept as text: an hour of "provider_unavailable" is the finding, not a footnote.
        workload.errors.append(str(payload.get("code")))
    elif kind == "turn.state" and workload._pending and payload.get("state") in ("idle", "speaking"):
        # The turn ended, whichever of the planner's outcomes it took. Only counted against a turn
        # this script asked for, so the brain's own idle state at connect does not inflate it.
        workload.turns_answered += 1
        workload._pending = False


def drive(workload: Workload, stop: threading.Event) -> None:
    """The driver thread. Reconnects rather than ending the soak, and counts that it had to."""

    async def run() -> None:
        first = True
        while not stop.is_set():
            try:
                await _drive_once(workload, stop)
            except Exception as error:  # noqa: BLE001 - any failure here is "the socket went away"
                if not first:
                    workload.reconnects += 1
                print(f"  driver: reconnecting after {type(error).__name__}: {error}")
                await asyncio.sleep(2.0)
            first = False

    asyncio.run(run())


def _measure(process: psutil.Process) -> dict[str, float] | None:
    try:
        info = process.memory_info()
        return {
            # Private bytes: what the process committed and has not returned. Not trimmable, so this
            # is the growth series. `rss` is the working set and is kept for continuity with P1/P2.
            "private_mib": info.private / BYTES_PER_MIB,
            "rss_mib": info.rss / BYTES_PER_MIB,
            "handles": float(process.num_handles()),
            "threads": float(process.num_threads()),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def trim_events(values: Sequence[float]) -> int:
    """How many times the series fell off a cliff between two consecutive samples."""
    return sum(
        1
        for previous, current in zip(values, values[1:], strict=False)
        if previous > 0 and current < previous * TRIM_DROP_FRACTION
    )


def _verdict(name: str, elapsed: list[float], series: dict[str, list[float]]) -> dict:
    private = series["private_mib"]
    rss = series["rss_mib"]
    handles = series["handles"]
    first_quarter, last_quarter = quartile_medians(private)
    private_slope = slope_per_hour(elapsed, private)
    handle_slope = slope_per_hour(elapsed, handles)

    return {
        "samples": len(private),
        "private_mib_slope_per_hour": round(private_slope, 3),
        "private_mib_median_first_quarter": round(first_quarter, 1),
        "private_mib_median_last_quarter": round(last_quarter, 1),
        "private_mib_max": round(max(private), 1),
        "handles_slope_per_hour": round(handle_slope, 2),
        "handles_max": int(max(handles)),
        "threads_max": int(max(series["threads"])),
        "bounded": private_slope < GROWTH_SLOPE_MB_PER_HOUR and handle_slope < HANDLE_SLOPE_PER_HOUR,
        # Reported, never decisive: a working set the OS trimmed says what the machine did.
        "rss_mib_max": round(max(rss), 1),
        "rss_working_set_trims": trim_events(rss),
        #: Every tenth sample, so the result file carries the shape and not only the summary.
        "private_mib_series": [round(value, 1) for value in private[::10]],
        "rss_mib_series": [round(value, 1) for value in rss[::10]],
    }


def main() -> int:
    parser = duration_argument(SPECIFIED_DURATION_S, SPECIFIED_SETTLE_S)
    parser.description = "Long-run growth profile under a real workload (M6 exit criterion)."
    parser.add_argument("--self-check", action="store_true", help="check the slope arithmetic and exit")
    arguments = parser.parse_args()

    if arguments.self_check:
        return self_check()

    report = Report(
        metric="soak",
        script="bench/soak.py",
        method=(
            f"{arguments.settle}s settle with the workload already running, then {arguments.duration}s "
            f"driven: a turn.request every {TURN_INTERVAL_S:.0f}s and a memory.reindex every "
            f"{REINDEX_INTERVAL_S:.0f}s, while private commit, working set, handles and threads are "
            f"sampled every {SAMPLE_INTERVAL_S:.0f}s. The verdict is the least-squares slope of "
            f"**private commit** per hour - not a first-to-last delta, and not working set, which the "
            f"OS trims. The counts below cover the measured window only."
        ),
    )
    warn_if_shortened(report, arguments.duration, SPECIFIED_DURATION_S, "measurement window")
    warn_if_shortened(report, arguments.settle, SPECIFIED_SETTLE_S, "settling period")

    processes = find_processes()
    if "system" not in processes or "brain" not in processes:
        print(f"Expected the sidecar and the brain to be running. Found: {sorted(processes)}", file=sys.stderr)
        print("Start them with: uv run python bench/run_stack.py", file=sys.stderr)
        return 2

    report.note(f"measured layers: {', '.join(sorted(processes))}")

    workload = Workload()
    stop = threading.Event()
    driver = threading.Thread(target=drive, args=(workload, stop), name="soak-driver", daemon=True)
    driver.start()

    print(f"Settling for {arguments.settle}s with the workload running...")
    settle(workload, arguments.settle, report)
    warmup = counters(workload)
    warmup_errors = len(workload.errors)

    load_start = memory_load_percent()
    loads: list[int] = []
    elapsed_s: list[float] = []
    series: dict[str, dict[str, list[float]]] = {
        name: {"private_mib": [], "rss_mib": [], "handles": [], "threads": []} for name in processes
    }
    vanished: set[str] = set()

    print(f"Soaking for {arguments.duration}s. Turns every {TURN_INTERVAL_S:.0f}s.")

    try:
        for elapsed in sample_clock(arguments.duration, SAMPLE_INTERVAL_S):
            loads.append(memory_load_percent())
            elapsed_s.append(elapsed)

            for name, process in processes.items():
                measured = _measure(process)
                if measured is None:
                    if name not in vanished:
                        report.note(f"{name} disappeared {elapsed:.0f}s into the run")
                        vanished.add(name)
                    continue

                for key, value in measured.items():
                    series[name][key].append(value)

            if int(elapsed) % 300 < SAMPLE_INTERVAL_S and elapsed > 1:
                brain_private = series["brain"]["private_mib"]
                print(
                    f"  {int(elapsed)}s  turns {workload.turns_answered}/{workload.turns_sent}  "
                    f"brain {brain_private[-1] if brain_private else float('nan'):.1f} MiB private"
                )
    except KeyboardInterrupt:
        report.note(f"interrupted after {elapsed_s[-1] if elapsed_s else 0:.0f}s of "
                    f"{arguments.duration}s; this run is indicative only")
    finally:
        stop.set()
        driver.join(timeout=10)

    load_swing = max(loads) - min(loads)
    report.note(f"system memory load {load_start}% -> {memory_load_percent()}%, swing {load_swing} points")

    measured = {key: value - warmup[key] for key, value in counters(workload).items()}
    report.values["workload"] = {
        **measured,
        "embeddings_available": workload.embeddings_available,
        "errors": sorted(set(workload.errors[warmup_errors:])),
        "turns_answered_while_settling": warmup["turns_answered"],
    }

    for name in processes:
        if not series[name]["rss_mib"]:
            continue
        # Layers can vanish mid-run; zip on the samples that exist for this one.
        report.values[name] = _verdict(name, elapsed_s[: len(series[name]["private_mib"])], series[name])
        rendered = report.values[name]
        print(
            f"\n{name}: {rendered['private_mib_slope_per_hour']:+.2f} MiB/h private  "
            f"q1 {rendered['private_mib_median_first_quarter']:.1f} -> "
            f"q4 {rendered['private_mib_median_last_quarter']:.1f} MiB  "
            f"max {rendered['private_mib_max']:.1f} MiB  handles {rendered['handles_slope_per_hour']:+.1f}/h  "
            f"working-set trims {rendered['rss_working_set_trims']}"
        )

    if load_swing > MEMORY_LOAD_TOLERANCE:
        # A note, not a discard - which is a deliberate departure from idle_rss.py, and the reason is
        # the metric. P1 and P2 report working set, so pressure invalidates them and the run has to
        # go. The verdict here is private commit, which the OS does not trim, so a load swing makes
        # the RSS series unreadable without touching the question this bench answers.
        report.note(
            f"system memory load moved {load_swing} points during the run. The RSS series is not "
            f"comparable across that and any working-set cliff in it is the OS reclaiming, not a "
            f"process freeing. The verdict above is private commit, which is unaffected."
        )

    if measured["turns_answered"] == 0:
        report.discard(
            "no turn was answered in the measured window, so the LLM, memory and capability paths were "
            "never exercised. A flat line produced by nothing happening is not evidence that "
            "something is bounded."
        )

    if workload.embeddings_available is False:
        report.note(
            "embeddings were unavailable, so recall ran keyword-only and the embedding path is NOT "
            "covered by this run. Start Ollama with OLLAMA_MODELS=E:\\LLMmodels."
        )

    unbounded = [name for name in processes if name in report.values and not report.values[name]["bounded"]]
    if unbounded:
        report.note(f"growth exceeded the guard in: {', '.join(unbounded)}")

    report.write()
    return 1 if report.discarded or unbounded else 0


def self_check() -> int:
    """The arithmetic, on series whose answer is known. Everything else here is I/O."""
    hour = [float(second) for second in range(0, 3600, 5)]

    flat = [50.0] * len(hour)
    assert abs(slope_per_hour(hour, flat)) < 1e-9, slope_per_hour(hour, flat)

    ramp = [50.0 + 10.0 * (second / 3600.0) for second in hour]
    assert abs(slope_per_hour(hour, ramp) - 10.0) < 1e-6, slope_per_hour(hour, ramp)

    # A plateau with one late collector pass: the endpoints say it fell, the slope says it is flat
    # to within a fraction of the guard. This is the case that motivated using a slope at all.
    plateau = [50.0] * (len(hour) - 1) + [46.0]
    assert -1.0 < slope_per_hour(hour, plateau) < 0.0, slope_per_hour(hour, plateau)

    # A step early in the run - a cache filling once - is growth that stops. Reported as a rate, it
    # must stay well under the guard rather than being extrapolated into a leak.
    step = [50.0] * 60 + [55.0] * (len(hour) - 60)
    assert 0 < slope_per_hour(hour, step) < GROWTH_SLOPE_MB_PER_HOUR, slope_per_hour(hour, step)

    first, last = quartile_medians(ramp)
    assert last > first, (first, last)
    assert slope_per_hour([1.0], [50.0]) == 0.0

    # The trim that discarded the first hour-long run: 61.8 MiB to 7.2 in one five-second step.
    trimmed = [61.8] * 300 + [7.2] + [17.9] * 100
    assert trim_events(trimmed) == 1, trim_events(trimmed)
    assert trim_events(ramp) == 0, trim_events(ramp)

    print("self-check passed: flat, ramp, plateau-with-collector-pass, early step, quartiles, trims")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
