"""M6 - fault injection: kill each layer at each stage and check §5 is telling the truth.

`docs/ARCHITECTURE.md` §5 states what each layer must do when another one dies. It has been a
document since M0 and an anecdote since M1, where one sidecar was killed by hand under a live
browser. This runs the four process-death rows systematically and writes a verdict per row.

**Three of §5's seven rows are not here, and are cited rather than redone.** A schema-invalid
message is covered by `InboundMessageParserTests`, `test_session.py` and `guards.test.ts`; a
throwing sensor by `SensorSweepTests`; the UI's own backoff and staleness marker by
`reducer.test.ts`. Re-testing them through a process kill would be a worse instrument for the same
claim.

**It starts and kills its own stack, by PID.** `find_processes()` matches on name, which is right
for measuring and wrong for killing - a developer's own sidecar running in another terminal must
not be collateral. Nothing here touches a process this script did not launch.

Run:
    uv run python bench/fault_injection.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

import psutil
import websockets
from _harness import BRAIN_WS_URL, Report, start_brain, start_sidecar

#: How long a layer is given to notice another one died. Generous: the point is whether it reports
#: at all, not how fast, and a tight bound would make this flaky rather than strict.
REPORT_TIMEOUT_S = 20.0

#: How long the sidecar is watched after the brain dies.
#:
#: Was 10 s, and 10 s could not answer the question. The first run showed +3.21 MB over that window
#: - under the threshold, but a rate that would have been unbounded buffering had it continued, and
#: two endpoints cannot tell a one-off allocation from a slope. Extended and sampled throughout: the
#: series shows a plateau at about +0.4 MB and then a collector pass taking it *below* where it
#: started, 51.81 -> 50.52 MB over a minute. The shape is the evidence; the threshold is only a
#: guard.
OBSERVE_S = 30.0

#: How often RSS is sampled across that window, so the report carries a shape rather than a delta.
OBSERVE_STEP_S = 3.0

#: The sidecar measured 0.167 % of one core at idle (PERFORMANCE.md §5). A reconnect loop that spun
#: would peg a core, so anything under this is "not spinning" with two orders of magnitude to spare.
SPIN_PERCENT = 5.0

#: Buffering samples unboundedly while disconnected would show as steady growth. 10 MiB over the
#: observation window is far more slack than a bounded buffer needs and far less than an unbounded
#: one would take.
BUFFER_GROWTH_MB = 10.0

_BYTES_PER_MB = 1024 * 1024


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


def kill_tree(process: subprocess.Popen) -> None:
    """Kills a process and everything it started, hard.

    ``uv run uvicorn`` makes the returned Popen a launcher rather than the process holding the
    socket, so killing the parent alone leaves the brain running and the port taken - which would
    make the next scenario measure a brain that never died. Children first, then the parent.

    ``kill`` rather than ``terminate``: this is simulating a crash, and a graceful shutdown is a
    different scenario than the one §5 describes.
    """
    try:
        parent = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        return

    for child in parent.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue

    parent.kill()
    process.wait(timeout=10)


def brain_process(launcher: subprocess.Popen) -> psutil.Process | None:
    """The process actually serving, rather than the uv launcher that started it."""
    try:
        parent = psutil.Process(launcher.pid)
    except psutil.NoSuchProcess:
        return None

    children = parent.children(recursive=True)
    return children[-1] if children else parent


async def wait_for_brain(timeout: float = 30.0) -> bool:
    """True once the brain accepts a handshake. Polls rather than sleeps a guessed interval."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with websockets.connect(BRAIN_WS_URL, open_timeout=2) as socket:
                await socket.send(client_hello())
                await asyncio.wait_for(socket.recv(), 5)
                return True
        except Exception:  # noqa: BLE001 - every failure here means "not up yet"
            await asyncio.sleep(0.5)

    return False


async def await_frame(socket, wanted: str, timeout: float = REPORT_TIMEOUT_S) -> dict | None:
    """The next frame of a given type, or None when none arrives in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            frame = json.loads(await asyncio.wait_for(socket.recv(), remaining))
        except (TimeoutError, asyncio.TimeoutError):
            return None

        if frame["type"] == wanted:
            return frame

    return None


async def scenario_sidecar_dies(report: Report) -> dict:
    """§5 row 1: the brain keeps serving and says the system layer is gone."""
    sidecar = start_sidecar(bench_mode=False)
    brain = start_brain()
    findings: dict[str, object] = {}

    try:
        if not await wait_for_brain():
            return {"pass": False, "detail": "the brain never came up"}

        async with websockets.connect(BRAIN_WS_URL) as socket:
            await socket.send(client_hello())
            sample = await await_frame(socket, "telemetry.sample")
            findings["telemetry_before"] = sample is not None

            kill_tree(sidecar)

            status = await await_frame(socket, "system.status")
            findings["status_frame_arrived"] = status is not None
            findings["connected_false"] = bool(status and status["payload"]["connected"] is False)
            # §5 requires a reason, not merely a flag: "the sidecar is gone" with no explanation is
            # the disconnected state the UI cannot say anything useful about.
            findings["reason_given"] = bool(status and status["payload"].get("reason"))
            findings["brain_still_alive"] = brain_process(brain) is not None

            # And it comes back. A disconnect that never recovers would satisfy every check above.
            sidecar = start_sidecar(bench_mode=False)
            back = await await_frame(socket, "system.status", timeout=30)
            findings["reconnected"] = bool(back and back["payload"]["connected"] is True)
            findings["telemetry_after"] = await await_frame(socket, "telemetry.sample") is not None
    finally:
        kill_tree(sidecar)
        kill_tree(brain)

    return {"pass": all(bool(value) for value in findings.values()), **findings}


async def scenario_brain_dies_with_sidecar_up(report: Report) -> dict:
    """§5 row 2: the sidecar does not exit, does not spin, does not buffer unboundedly."""
    sidecar = start_sidecar(bench_mode=False)
    brain = start_brain()
    findings: dict[str, object] = {}

    try:
        if not await wait_for_brain():
            return {"pass": False, "detail": "the brain never came up"}

        async with websockets.connect(BRAIN_WS_URL) as socket:
            await socket.send(client_hello())
            await await_frame(socket, "telemetry.sample")

        watched = psutil.Process(sidecar.pid)
        rss_before = watched.memory_info().rss / _BYTES_PER_MB

        kill_tree(brain)

        # cpu_percent over an interval, not a snapshot: a reconnect loop is only visible as work
        # done over time.
        watched.cpu_percent(interval=None)

        # Sampled across the window rather than at its ends. A delta between two points cannot tell
        # a plateau from a slope, and "does not buffer unboundedly" is a claim about the slope.
        series: list[float] = []
        deadline = time.monotonic() + OBSERVE_S
        while time.monotonic() < deadline:
            series.append(round(watched.memory_info().rss / _BYTES_PER_MB, 2))
            time.sleep(OBSERVE_STEP_S)

        cpu = watched.cpu_percent(interval=None)
        rss_after = watched.memory_info().rss / _BYTES_PER_MB

        findings["sidecar_still_running"] = watched.is_running()
        findings["cpu_percent"] = round(cpu, 3)
        findings["not_spinning"] = cpu < SPIN_PERCENT
        findings["rss_mb_series"] = series
        findings["rss_growth_mb"] = round(rss_after - rss_before, 2)
        # Against the peak, not the last sample: a collector pass that happens to land just before
        # the final read would hide a window in which the buffer really did grow.
        findings["not_buffering"] = (max(series) - rss_before) < BUFFER_GROWTH_MB

        # Back to waiting: a fresh brain must be able to attach to the same pipe.
        brain = start_brain()
        findings["accepts_a_new_consumer"] = await wait_for_brain()
    finally:
        kill_tree(sidecar)
        kill_tree(brain)

    return {
        "pass": all(
            bool(findings.get(key))
            for key in ("sidecar_still_running", "not_spinning", "not_buffering", "accepts_a_new_consumer")
        ),
        **findings,
    }


async def scenario_brain_dies_with_ui_up(report: Report) -> dict:
    """§5 row 3: the socket closes rather than hanging, and reconnects afterwards."""
    sidecar = start_sidecar(bench_mode=False)
    brain = start_brain()
    findings: dict[str, object] = {}

    try:
        if not await wait_for_brain():
            return {"pass": False, "detail": "the brain never came up"}

        socket = await websockets.connect(BRAIN_WS_URL)
        await socket.send(client_hello())
        await await_frame(socket, "telemetry.sample")

        kill_tree(brain)

        # A socket that hangs is the failure §5 names: the UI would sit on a dead connection
        # showing numbers that stopped changing, which is indistinguishable from an idle machine.
        try:
            await asyncio.wait_for(socket.recv(), REPORT_TIMEOUT_S)
            findings["socket_closed"] = False
        except websockets.exceptions.ConnectionClosed:
            findings["socket_closed"] = True
        except (TimeoutError, asyncio.TimeoutError):
            findings["socket_closed"] = False
        finally:
            await socket.close()

        brain = start_brain()
        findings["reconnects_after_restart"] = await wait_for_brain()
    finally:
        kill_tree(sidecar)
        kill_tree(brain)

    return {"pass": all(bool(value) for value in findings.values()), **findings}


async def scenario_ui_closes(report: Report) -> dict:
    """§5 row 4, and the decision it records: the sidecar keeps sweeping with no tab attached.

    The brain deliberately does not drop the pipe when the last UI closes - reconnecting would cost
    a handshake and a PDH warm-up tick. So `seq` must have advanced while nothing was watching, and
    a reconnect that finds it unchanged would mean the sweep had stopped.
    """
    sidecar = start_sidecar(bench_mode=False)
    brain = start_brain()
    findings: dict[str, object] = {}

    try:
        if not await wait_for_brain():
            return {"pass": False, "detail": "the brain never came up"}

        async with websockets.connect(BRAIN_WS_URL) as socket:
            await socket.send(client_hello())
            first = await await_frame(socket, "telemetry.sample")

        if first is None:
            return {"pass": False, "detail": "no telemetry arrived before the tab closed"}

        seq_before = first["payload"]["seq"]
        away_s = 8.0
        time.sleep(away_s)

        async with websockets.connect(BRAIN_WS_URL) as socket:
            await socket.send(client_hello())
            second = await await_frame(socket, "telemetry.sample")

        findings["brain_still_alive"] = brain_process(brain) is not None
        findings["seq_before"] = seq_before
        findings["seq_after"] = second["payload"]["seq"] if second else None
        advanced = bool(second) and second["payload"]["seq"] > seq_before
        findings["kept_sweeping"] = advanced

        if advanced:
            # Roughly one per second while away. Reported rather than asserted: the exact count
            # depends on scheduling, and demanding a number would make this flaky.
            report.note(
                f"seq advanced by {second['payload']['seq'] - seq_before} over {away_s:.0f}s with "
                f"no tab attached"
            )
    finally:
        kill_tree(sidecar)
        kill_tree(brain)

    return {"pass": all(bool(findings.get(key)) for key in ("brain_still_alive", "kept_sweeping")), **findings}


SCENARIOS = (
    ("sidecar_dies_brain_up", scenario_sidecar_dies),
    ("brain_dies_sidecar_up", scenario_brain_dies_with_sidecar_up),
    ("brain_dies_ui_up", scenario_brain_dies_with_ui_up),
    ("ui_closes", scenario_ui_closes),
)


async def run() -> int:
    report = Report(
        metric="fault-injection",
        script="bench/fault_injection.py",
        method=(
            "Starts its own sidecar and brain per scenario, kills one by PID, and checks the "
            "surviving layers against docs/ARCHITECTURE.md section 5. The harness plays the UI "
            "over a real WebSocket."
        ),
    )

    for name, scenario in SCENARIOS:
        print(f"\n{name}")
        outcome = await scenario(report)
        report.values[name] = outcome
        print(f"  {'PASS' if outcome.get('pass') else 'FAIL'}  {outcome}")

    failed = [name for name, _ in SCENARIOS if not report.values[name].get("pass")]
    if failed:
        report.note(f"failed scenarios: {', '.join(failed)}")

    report.write()
    print(f"\n{len(SCENARIOS) - len(failed)}/{len(SCENARIOS)} scenarios behaved as section 5 requires.")
    return 1 if failed else 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
