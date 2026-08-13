# bench

Measurement harness. The rule these scripts exist to serve is in `docs/PERFORMANCE.md`:

> No performance claim without a script in `bench/` that produced it.

A number written into `PERFORMANCE.md` §5 names the script that produced it and the date it was
taken. Nothing in this directory estimates, and a run taken under conditions its method excludes is
discarded rather than reported.

## Running

Two of the four need the stack up in the state a user would have it in. Start it in one terminal:

```
uv run python bench/run_stack.py
```

Then, in another:

| Script | Budget | Needs the stack | Wall time |
|---|---|---|---|
| `bench/idle_rss.py` | P1, P2 | yes | ~6 min (60 s settle + 300 s) |
| `bench/idle_cpu.py` | P5 | yes | ~5 min |
| `bench/ws_latency.py` | P4 | yes | ~10 min |
| `bench/poll_latency.py` | P3 | **no** — it starts its own sidecar | ~10 min |
| `bench/reindex_incremental.py` | M4.5 exit criterion, not a P-budget | **no** — temporary vault, library only | ~10 s |
| `bench/soak.py` | M6 exit criterion, not a P-budget | yes, **and Ollama** | ~64 min (180 s settle + 60 min) |
| `bench/fault_injection.py` | M6 exit criterion, not a P-budget | **no** — starts and kills its own stack | ~4 min |

`reindex_incremental.py` is the odd one out: it answers a ROADMAP exit criterion rather than a
`PERFORMANCE.md` budget. M4.5 asks that incremental reindex "touches only changed files, **measured**
rather than asserted", and a test asserting `indexed == 1` would prove the counter says one, not that
the second scan is cheaper. It reports counts and wall time for a cold build and a rescan, with and
without embeddings — only the embedded figure covers the criterion, because re-embedding is where a
broken incremental path actually costs. When Ollama is not answering it says so rather than reporting
the keyword number as though it counted.

`soak.py` and `fault_injection.py` answer M6's exit criteria the same way `reindex_incremental.py`
answers M4.5's. `soak.py` drives a real workload while it samples — a turn every two minutes and a
reindex every ten — because an idle hour would re-measure P1 and P2 and leave the planner, the
reader, the embedding index and the capability path untouched. It reports growth as a least-squares
slope per hour rather than a first-to-last delta, and **discards a run in which no turn was
answered**: a flat line produced by nothing happening is not evidence that anything is bounded.
`fault_injection.py` is the only script here that starts and kills processes, and it kills by PID so
a developer's own sidecar in another terminal is never collateral.

`poll_latency.py` runs the sidecar itself, with `LOCALZERO_BENCH=1` so it emits per-tick sweep
durations to stderr. That switch is off in normal operation: the IPC contract has no field for a
sweep duration, correctly, and adding one for a benchmark's convenience would be the wrong reason
to change a contract.

Every script accepts `--duration` for a shorter run. **A shortened run is recorded as
non-conforming in its own output and must not be quoted in `PERFORMANCE.md`** — the note says so in
the results file, so a number lifted out of one carries its own disclaimer.

## Results

Each run writes a JSON file to `bench/results/`, which is gitignored. The document holds the
numbers; the directory holds their provenance.

## Traps these scripts are built around

Each is named in `PERFORMANCE.md` §2 and is the reason the script is more than a stopwatch.

- **P3** — the first PDH reads after opening a query return no usable value, because a rate counter
  needs two collections before it has one. The first five sweeps are discarded and the output lists
  them, so an implausibly good p95 cannot be assembled out of sweeps that measured nothing.
- **P1/P2** — Windows working set responds to system memory pressure and can shrink without the
  process freeing anything. Memory load is recorded throughout and the run is discarded if it moved
  more than 10 points.
- **P4** — the metric is only as good as the clock. This harness is a local Python client sharing
  the system clock the sidecar stamps `sampled_at` from, so no cross-origin offset is needed and
  none is faked. **The browser leg is not measured** and the number must not be read as if it were.
- **P5** — measuring utilization through a counter risks the localized-counter bug (invariant L1)
  landing in the bench itself. This reads CPU *time* per process from the OS instead, so there is
  no counter path to get wrong.
