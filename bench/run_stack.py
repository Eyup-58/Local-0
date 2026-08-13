"""Starts the stack and holds it up while a bench script measures it.

The measuring scripts deliberately do not start what they measure. A harness that owns the
processes is a harness that can accidentally measure its own supervision, and P1, P2 and P5 all
want the stack in the state a user would have it in - started, settled, and left alone.

**Two processes since M7, not three**, because the brain serves the built UI itself. This is not
run.py: the product opens a browser tab, and a bench that started one would be measuring the
browser as well as the thing under test.

Run in one terminal:
    uv run python bench/run_stack.py

Then in another:
    uv run python bench/idle_rss.py

Ctrl+C here stops both.
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

#: The brain serves the built UI at its own port since M7. There is no third process to start.
BRAIN_PORT = 8765


def main() -> int:
    require_built_sidecar()

    print("Starting the sidecar and the brain...")
    processes = [start_sidecar(bench_mode=False), start_brain()]

    if not (REPOSITORY_ROOT / "ui" / "dist" / "index.html").is_file():
        print("ui/dist not found; the brain will serve no page. Run `npm run build` in ui/.")

    try:
        # Give them a moment, then report what a bench script would find. A layer that failed to
        # start is worth knowing about now rather than five minutes into a measurement.
        time.sleep(5)
        found = find_processes()
        print(f"visible to the bench scripts: {', '.join(sorted(found)) or 'nothing'}")
        print(f"\nUI at http://127.0.0.1:{BRAIN_PORT} — open it so the stack is in its real state.")
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
