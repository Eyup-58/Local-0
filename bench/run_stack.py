"""Starts the three layers and holds them up while a bench script measures them.

The measuring scripts deliberately do not start what they measure. A harness that owns the
processes is a harness that can accidentally measure its own supervision, and P1, P2 and P5 all
want the stack in the state a user would have it in - started, settled, and left alone.

Run in one terminal:
    uv run python bench/run_stack.py

Then in another:
    uv run python bench/idle_rss.py

Ctrl+C here stops all three.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from _harness import (
    REPOSITORY_ROOT,
    find_processes,
    require_built_sidecar,
    start_brain,
    start_sidecar,
)

UI_PORT = 5173


def start_ui() -> subprocess.Popen[bytes] | None:
    """Serves the built UI. Skipped when it has not been built - P5 then says so."""
    dist = REPOSITORY_ROOT / "ui" / "dist"
    if not dist.is_dir():
        print("ui/dist not found; skipping the UI. Run `npm run build` in ui/ to include it.")
        return None

    return subprocess.Popen(
        [
            "node", "node_modules/vite/bin/vite.js", "preview",
            "--host", "127.0.0.1", "--port", str(UI_PORT), "--strictPort",
        ],
        cwd=REPOSITORY_ROOT / "ui",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    require_built_sidecar()

    print("Starting the sidecar, the brain and the UI...")
    processes = [start_sidecar(bench_mode=False), start_brain()]

    ui = start_ui()
    if ui is not None:
        processes.append(ui)

    try:
        # Give them a moment, then report what a bench script would find. A layer that failed to
        # start is worth knowing about now rather than five minutes into a measurement.
        time.sleep(5)
        found = find_processes()
        print(f"visible to the bench scripts: {', '.join(sorted(found)) or 'nothing'}")
        print(f"\nUI at http://127.0.0.1:{UI_PORT} — open it so the stack is in its real state.")
        print("Ctrl+C to stop.\n")

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    return 0


if __name__ == "__main__":
    if os.name != "nt":
        print("Local Zero is a Windows product.", file=sys.stderr)
        raise SystemExit(2)

    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
