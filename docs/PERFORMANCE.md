# Local Zero — Performance

**Status:** M0. **Every budget on this page is PROVISIONAL.** They are starting hypotheses, not
measurements, and they are labelled as such until M1 replaces them with real numbers.

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

## 1. Provisional budgets

Starting hypotheses. **Not yet measured. Replace in M1.**

| # | Metric | Provisional budget | Produced by | Status |
|---|---|---|---|---|
| P1 | system sidecar idle RSS | < 80 MB | `bench/idle_rss.py` | not measured |
| P2 | brain idle RSS | < 150 MB | `bench/idle_rss.py` | not measured |
| P3 | Telemetry poll duration, p95 | < 50 ms | `bench/poll_latency.py` | not measured |
| P4 | WS message latency, p95 | < 20 ms | `bench/ws_latency.py` | not measured |
| P5 | Total idle CPU, all three processes | *to be set from the measured floor* | `bench/idle_cpu.py` | not measured |

P5 has no number on purpose. Setting a CPU budget before measuring the floor produces either a
figure that is trivially met or one that is unreachable for reasons the design cannot influence.
M1 measures first, then sets it.

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

Empty. M1 fills this in with real output, quoted verbatim from the bench scripts, each with its
date.

| Date | Metric | Value | Script | Verdict |
|---|---|---|---|---|
| — | — | — | — | *no measurements taken yet* |
