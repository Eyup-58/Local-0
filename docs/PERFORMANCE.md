# Local Zero — Performance

**Status:** M1. The budgets below have been **measured** and revised against the floor. §5 carries
the numbers, the scripts that produced them, and — as importantly — what they do not cover.

---

## 0. The rule

**No performance claim without a script in `bench/` that produced it.**

- "Fast", "lightweight", "efficient", "minimal overhead" are not acceptable statements. They carry
  no information and cannot be falsified.
- A number written in this document must name the script that produced it and the date it was
  taken.
- If a metric cannot be measured, it is recorded as **"not measured"** with the reason. It is never
  estimated and never quietly dropped.
- A budget that is missed is recorded as missed. The response is either to fix the code or to
  renegotiate the budget against the measured floor — never to edit the number and move on.

This rule exists because a tool that reports on machine resource use has no credibility if it is
casual about its own.

---

## 1. Budgets

Revised 2026-08-11 against the measurements in §5. Each is now a regression guard rather than a
guess: set with enough headroom for ordinary variation, and tight enough that a real regression
trips it. The original M0 hypothesis is kept alongside so the revision is visible.

| # | Metric | Budget | M0 hypothesis | Measured | Produced by |
|---|---|---|---|---|---|
| P1 | system sidecar idle RSS | < 80 MiB | < 80 MB | 59.3 MiB max | `bench/idle_rss.py` |
| P2 | brain idle RSS | **< 80 MiB** | < 150 MB | 51.4 MiB max | `bench/idle_rss.py` |
| P3 | Telemetry poll duration, p95 | **< 10 ms** | < 50 ms | 2.00 ms | `bench/poll_latency.py` |
| P4 | Latency to a local consumer, p95 | < 20 ms | < 20 ms | 4.10 ms | `bench/ws_latency.py` |
| P5 | Total idle CPU, all three processes | **< 1.0 % of one logical processor** | *unset by design* | 0.302 % | `bench/idle_cpu.py` |

What changed and why:

- **P2 tightened from 150 to 80 MiB.** The hypothesis was three times the measured floor, which
  would have let the brain triple in size without anyone noticing. FastAPI, Pydantic and a reader
  thread cost 51 MiB; 80 leaves room for the guard chain M2 adds.
- **P3 tightened from 50 to 10 ms.** A budget 25× the measured p95 cannot fail for any reason worth
  hearing about. 10 ms still leaves five times the current cost.
- **P4 left at 20 ms**, deliberately, even though the measured segment is 4.10 ms. The budget is
  written against the *whole* path to the UI and only part of that path has been measured — see §5.
  Tightening it against a partial measurement would be pretending the rest is free.
- **P5 set to 1.0 % of one logical processor**, roughly three times the measured floor. Note the
  unit: one logical processor, not the machine. On this 28-thread CPU the measured 0.302 % is about
  0.011 % of total capacity.

### Why p95 and not average

An average hides the stall. A sampler that completes in 3 ms most ticks and 400 ms occasionally has
a fine average and a visibly janky readout. The tail is the number that corresponds to what a human
notices, so the tail is what gets a budget.

---

## 2. How each metric is measured

Definitions are written before the code so the number cannot be quietly redefined into passing.

### P1, P2 — idle RSS

**Definition.** Working set of each process after startup has settled, with the UI connected and
telemetry streaming, and no user interaction.

**Method.** Start all three layers. Wait 60 s for settling. Sample working set once per second for
300 s. Report median and max. **Report the max against the budget**, not the median — a process
that transiently doubles is a process that will be observed doing so.

**Trap.** Windows working set is not a clean allocation measure; it responds to system memory
pressure and can shrink without the process freeing anything. The run must record whether memory
pressure occurred, and a run under pressure is discarded, not reported.

### P3 — telemetry poll duration

**Definition.** Wall time inside the sidecar from the start of a sensor sweep to a complete
`telemetry.sample` ready to write. Excludes pipe write and everything downstream.

**Method.** The sidecar records the duration per tick. `bench/poll_latency.py` collects at least
600 consecutive samples (10 minutes at 1 Hz) and reports p50 / p95 / p99 / max.

**Trap.** The first few PDH reads after opening a counter query return garbage or zero — PDH needs
two samples to compute a rate. The bench discards the first 5 samples and says so in its output.
A run that silently included them would report an implausibly good p95.

### P4 — WebSocket latency

**Definition.** From `sampled_at` in the payload to the moment the UI's handler receives the frame.

**Method.** The UI records receipt time against the embedded `sampled_at`. Over 600 samples, report
p50 / p95 / p99.

**Trap — this metric is only as good as the clock.** Both ends are on the same machine, so
`performance.now()` versus a .NET timestamp is a comparison across two different time origins. The
bench must establish an offset first (a round-trip probe) or report the metric as **not measured**.
Reporting a negative or suspiciously-zero latency as success is the failure mode here.

### P5 — idle CPU

**Definition.** Sum of CPU time across all three processes over a fixed window, divided by wall
time, with the UI visible and telemetry streaming.

**Method.** 300 s window, no user interaction. Report per-process and total.

**Trap.** Must be measured with the same English-counter path the product uses (invariant L1),
otherwise the bench half-works on this machine exactly the way the product would.

---

## 3. Inherited unknowns

**U2 — a visible browser tab composites regardless of what the page draws.**
Project 0 recorded this against WebView2 and never measured it. Local Zero is less exposed, since
the UI is an ordinary browser tab the user already has open rather than a bundled webview — but the
question does not disappear, it moves. Before the UI grows any animation, measure a blank page's
idle cost so the baseline is known and the budget is set against the real floor.

**Sampling cost of PDH multi-instance counters.**
`GPU Engine(*)` returned 8 active instances on this machine (M2). Wildcard instance enumeration is
not free and the cost scales with instance count, which varies with what is running. P3's window
must include at least one period with a game or GPU workload active, or it measures the easy case
only.

---

## 4. What is deliberately not budgeted in v1

- **Startup time.** Three processes launching is not on a hot path a user waits behind repeatedly.
  Revisit if it exceeds a few seconds.
- **Binary and bundle size.** Nothing is distributed yet. Meaningless until packaging exists.
- **LLM inference latency.** Dominated by the model and the hardware, not by Local Zero's code.
  When M4 lands, what is worth measuring is the *overhead Local Zero adds* around the call — guard,
  validation, audit write — not the call itself.
- **Throughput.** There is one machine, one user, and a 1 Hz stream. There is no load to scale to,
  and a throughput number here would be theatre.

---

## 5. Results

Measured 2026-08-11 on the target machine, i7-14700KF / RX 7800 XT / 64 GB, with all three layers
running and the UI open in a browser. Raw output for each run is in `bench/results/`.

| Date | Metric | Value | Script | Verdict |
|---|---|---|---|---|
| 2026-08-11 | P1 system idle RSS | median 58.8 MiB, **max 59.3 MiB** | `bench/idle_rss.py` | **PASS** (budget < 80 MiB) |
| 2026-08-11 | P2 brain idle RSS | median 51.4 MiB, **max 51.4 MiB** | `bench/idle_rss.py` | **PASS** (budget < 150 MiB) |
| 2026-08-11 | P3 sweep duration | p50 1.34 ms, **p95 2.00 ms**, p99 2.32 ms, max 3.44 ms | `bench/poll_latency.py` | **PASS** (budget < 50 ms) |
| 2026-08-11 | P4 latency, sidecar → local consumer | p50 2.76 ms, **p95 4.10 ms**, p99 4.63 ms, max 5.05 ms | `bench/ws_latency.py` | **PASS for the measured segment** (budget < 20 ms) |
| 2026-08-11 | P5 total idle CPU | **0.302 % of one logical processor** | `bench/idle_cpu.py` | budget set from this floor |
| 2026-08-13 | P4 latency, re-measured at M6 | p50 5.46 ms, **p95 7.07 ms**, p99 7.50 ms, max 7.99 ms | `bench/ws_latency.py` | **PASS for the measured segment** (budget < 20 ms), and **slower than 2026-08-11** — see below |
| 2026-08-13 | Long-run growth, 60 min driven (M6, not a P-budget) | private commit **-0.42 / +0.00 / +0.00 MiB per hour** (system / brain / ui), handles +6.8 / 0 / 0 per hour | `bench/soak.py` | **PASS** (guards: 10 MiB/h, 100 handles/h) over 30/30 answered turns |

Verbatim:

```
P3 sweep duration: n=600  p50=1.34ms  p95=2.00ms  p99=2.32ms  max=3.44ms
  note: discarded the first 5 sweeps as PDH warm-up: 12.25ms, 8.05ms, 1.92ms, 1.48ms, 1.70ms

brain:  median 51.4 MiB, max 51.4 MiB (n=300)
system: median 58.8 MiB, max 59.3 MiB (n=300)
ui:     median 72.0 MiB, max 72.2 MiB (n=300)
  note: system memory load 22% -> 22%, swing 0 points

brain:  0.39s CPU over 300.0s wall = 0.130% of one core
system: 0.50s CPU over 300.0s wall = 0.167% of one core
ui:     0.02s CPU over 300.0s wall = 0.005% of one core
P5 total idle CPU: 0.302% of one logical processor

P4 latency (excluding the browser): n=600  p50=2.76ms  p95=4.10ms  p99=4.63ms  max=5.05ms

system: -0.42 MiB/h private  q1 34.2 -> q4 33.9 MiB  max 34.5 MiB  handles +6.8/h  working-set trims 0
brain:  +0.00 MiB/h private  q1 45.3 -> q4 45.3 MiB  max 45.3 MiB  handles +0.0/h  working-set trims 0
ui:     +0.00 MiB/h private  q1 104.9 -> q4 104.9 MiB max 104.9 MiB handles +0.0/h  working-set trims 0
  workload: 30/30 turns answered, 6 reindexes, 3600 telemetry frames, 37 tool logs, 0 errors
```

### P4 got slower between M1 and M6, and that is recorded rather than smoothed

The same script, the same 600 frames, the same machine: **p95 4.10 ms on 2026-08-11, 7.07 ms on
2026-08-13** (`bench/results/P4-ws-latency-20260813T003628Z.json`). Both are inside a 20 ms budget
and neither is a failure, but a budget with four times the headroom is exactly the budget a
regression hides in, so the number is written down as it came out.

**No cause is claimed.** The brain gained a capability registry, a guard chain, an audit log, a
memory index and a provider layer between those two dates, and any of them could plausibly cost a
few milliseconds per frame — but "plausibly" is not a measurement, and nothing here has profiled the
per-frame path to say which. What is measured is the delta. Attributing it would be narration.

What this does change: **P4 is now re-measured at every milestone boundary**, not only when
something is expected to have moved. A second data point is what turned this from an unremarkable
number into a known one.

Recorded because a measurement quoted beyond its conditions is worse than no measurement.

**P3 measured the idle case only.** §3 requires the P3 window to include a period with a real GPU
workload, because `GPU Engine(*)` instance enumeration is not free and its cost scales with how
many instances exist. This run was taken on an idle machine. **The loaded case is not measured**,
and the 2.00 ms p95 must not be assumed to hold with a game running.

**P4 excludes the browser.** The number is sidecar sweep completion through the pipe, the brain and
the WebSocket to a local Python client sharing the system clock — so no cross-origin offset was
needed and none was faked. Whatever the browser adds on top is **not measured**. The full
`sampled_at` → rendered-frame path stays unmeasured until something instruments the UI itself.

**U2 is still not measured.** The idle cost of a visible browser tab compositing regardless of what
the page draws was inherited from Project 0 and remains unquantified. The 72.0 MiB and 0.005 % above
are the vite preview server, **not** the browser tab rendering the panel.

### The long-run profile measures private commit, because working set answered a different question

M6 asks whether an extended session grows without bound. The first hour-long run of `bench/soak.py`
sampled working set, and 43 minutes in it recorded this: the brain fell from **61.8 MiB to 7.2**, the
UI from **68.4 to 1.5**, and the sidecar from **63.8 to 7.5** — all in the same five-second step, then
all climbing back as pages were faulted in
(`bench/results/soak-20260813T120642Z.json`, discarded).

Three processes do not release 85 % of their memory simultaneously. The machine reclaimed it, which
is the trap `§2` already names for P1 and P2 — and the run discarded itself on the memory-load
tolerance, as designed. But the deeper problem is that the tolerance was protecting the wrong metric:
**working set is a statement about what is resident under current pressure**, and no threshold on it
can answer "is this growing without bound" on a machine that also runs a 17 GB model.

The soak now decides on **private commit** — what a process asked for and has not given back, which
the OS does not trim. Working set is still sampled and reported, since P1 and P2 are written against
it, and any cliff in that series is now counted as a trim event so a reader is not left to guess
what the drop meant. The memory-load swing became a note rather than a discard for the same reason:
it makes the RSS series unreadable and leaves the verdict untouched.

`idle_rss.py` keeps its discard rule unchanged. P1 and P2 *are* working-set budgets, so pressure
genuinely invalidates them.

### A wrong number caught before it was written down

The first P5 run reported the brain at 5.4 MiB using **0.000 s** of CPU over five minutes. A process
validating and broadcasting a sample every second cannot do that. The cause was process selection:
`uv run uvicorn …` produces a `uv.exe` parent whose command line matches the child's just as well,
and the harness had picked the idle wrapper.

Both scripts were fixed to select the interpreter, and `bench/idle_cpu.py` now refuses to report a
working layer at zero CPU without flagging the run as suspect. The numbers above are from the rerun.
This is recorded rather than quietly corrected, because a benchmark that can silently measure the
wrong process once can do it again.
