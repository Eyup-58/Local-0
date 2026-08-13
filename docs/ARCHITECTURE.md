# Local Zero — Architecture

**Status:** written in M0 as a decision document; **M0–M4.5 are built and gated**, M5 is in progress.
The decisions below are the ones the code was built against, and where measurement has since
contradicted or confirmed one, that is recorded here rather than left to be inferred from the diff.
**Target:** Windows 11 x64, single user, local only. i7-14700KF / RX 7800 XT 16 GB / 64 GB DDR5.
**Scope:** layer boundaries, runtime justification, privilege model, sensor strategy, IPC choice,
failure behaviour.

Design decisions here are binding on M1–M7. Where a decision is genuinely open it is marked
**OPEN QUESTION** rather than guessed at.

> **Predecessor.** Local Zero supersedes `E:\Project 0`, a Windows telemetry panel that reached a
> documented architecture pass and no code. Its measured findings are carried forward in §0 with
> attribution. Project 0 is retired; its directory is read-only reference and receives no further
> work.

---

## 0. Environment as measured

Probed on the target machine on 2026-08-11, not assumed.

| Component | State |
|---|---|
| .NET SDK | **10.0.302** — installed 2026-08-11 via winget. Runtimes 10.0.10 (NETCore, AspNetCore, WindowsDesktop) |
| MSVC | Visual Studio Build Tools 2022 — installed |
| Node / npm | v22.23.2 / 12.0.2 — installed, `ignore-scripts=false` |
| Python | 3.11.15 on PATH (an unrelated agent venv), `uv` 0.12.3 |
| Git | 2.55.0 — installed, **global user.name / user.email unset** |
| Ollama | installed, `OLLAMA_MODELS=E:\ModelFile` |

The .NET SDK was absent when this document was first written and was installed the same day. M1 is
no longer blocked on toolchain.

### Findings inherited from Project 0

These were measured on this same machine and remain true. They are the reason several decisions
below are not the obvious ones.

**L1 — this machine's performance counter names are Turkish.**
`Get-Counter -ListSet` reports `İşlemci Bilgileri` and `İşlemci`. The English path
`\Processor(_Total)\% Processor Time` fails with "object not found". The GPU counter sets
(`GPU Engine`, `GPU Adapter Memory`, `GPU Process Memory`) happen to have remained English.

This is the nastiest finding in the set, because a naive implementation **half works**: GPU telemetry
appears, CPU telemetry silently returns nothing, and that reads as a bug in the CPU sampler. It
costs hours before anyone suspects locale.

> **BUILD INVARIANT L1.** Never resolve a performance counter by localized name. Use
> `PdhAddEnglishCounterW` via P/Invoke, which resolves English names under any system locale.
> .NET's `System.Diagnostics.PerformanceCounter` uses localized names and is therefore **banned**
> in this repository. This applies to GPU counters too, even though they read English on this box
> today — that is luck, not a guarantee.

**M2 — GPU counters work unelevated.** `\GPU Engine(*)\Utilization Percentage` returned 8 active
engine instances and `\GPU Adapter Memory(*)\Dedicated Usage` returned 3099 MB, both from a
non-admin shell. Driverless GPU load and VRAM telemetry is real and needs no privilege escalation.

**M1 — the WMI thermal path is dead.** `MSAcpi_ThermalZoneTemperature` returns Access Denied
unelevated, and on consumer desktop boards it typically exposes a single ACPI zone reporting the
chipset rather than the CPU. It costs an elevation prompt to obtain a number that is wrong. Treat
it as unavailable.

**U1 — AMD ADLX, resolved 2026-08-13. GPU temperature is real.** ADLX is the only driverless route
to GPU temperature, hotspot, fan RPM and board power, and it was carried as unvalidated from M0
because a C interface makes interop *plausible* rather than measured. The M5 spike measured it:
`amdadlx64.dll` 1.5.0.124, on a Radeon RX 7800 XT, **unelevated throughout**.

The C interface is COM-shaped without being COM — calls go through vtable slots by index, so a
wrong index returns a plausible-looking double that is not a temperature, which is invariant 10's
exact failure. The layout was therefore proved before any value was believed:

| Check | Result |
|---|---|
| `IADLXSystem` slot 10 `TotalSystemRAM` | 65302 MB against the 63.8 GiB Windows reports |
| Metrics `GPUVRAM` against PDH `Dedicated Usage` | **2942 MB against 2942 MB**, same second |
| Metrics `GPUUsage` against PDH `GPU Engine` | 23.0 % against 22.0 % |
| Temperature under changing load | 49 °C at 8 % → 51 °C at 23 %, hotspot above edge |

The exact VRAM match is what settles it: a neighbouring slot landing on the right value proves the
metrics object and its offsets, and therefore that the temperature slot is the temperature.

Board power returned a non-zero result code — this card does not report it — and is rendered as a
gap rather than a zero. Fan RPM read 0 at idle, which is a fan-stop, not a missing sensor.

**What the spike did not establish:** `IADLXList` slot 3 returned 0 for a list whose element 0 was
then fetched successfully, so it is not `Size`, or not with that signature. It is unused and stays
unused until somebody measures what it is.

---

## 1. The three layers

```
┌────────────────────┐   named pipe    ┌────────────────────┐   websocket   ┌──────────────┐
│  system  (C#)      │  ndjson, ACL    │  brain  (Python)   │  ndjson       │  ui  (TS)    │
│                    │ ──────────────▶ │                    │ ────────────▶ │              │
│  · PDH sensors     │                 │  · capability      │               │  · telemetry │
│  · process scan    │                 │    registry+guard  │               │    view      │
│  · OS operations   │ ◀────────────── │  · approval queue  │ ◀──────────── │  · approval  │
│                    │   capability    │  · LLM routing     │   decisions   │    dialog    │
│  NO shell. NO      │   invocations   │  · audit log       │               │  NO markdown │
│  elevation.        │                 │                    │               │  NO html     │
└────────────────────┘                 └────────────────────┘               └──────────────┘
        asInvoker                            asInvoker                        asInvoker
```

Responsibility boundaries, stated so they are not negotiated later:

- **system** knows how to read this machine and how to act on it. It knows nothing about LLMs,
  planning, or approval policy. It never decides *whether* an action should happen — it reports
  what it can do and executes what the brain has already had approved.
- **brain** decides. It owns the capability registry, the guard chain, the approval queue, the
  audit log, and LLM routing. It is the only layer that talks to a model.
- **ui** renders and collects a human decision. It holds no authority: it cannot construct a
  capability invocation, only approve or reject one the brain has already resolved.

### Why each runtime is earned

This project pays for three runtimes. That is a real cost — three dependency systems, three crash
formats, three packaging stories — and it needs justifying rather than assuming.

Project 0 reached the opposite conclusion on the same machine and chose a single Rust runtime. It
was right *for that product*: a read-only telemetry panel has nothing for a second runtime to do.
Local Zero is a different product. The difference that changes the answer is that Local Zero
orchestrates models and executes OS actions behind a guard, and that work has a natural home.

| Layer | Runtime | What it buys that the others cannot give cheaply |
|---|---|---|
| system | .NET 10 | First-class access to Win32: PDH, process enumeration, named pipe ACLs via `NamedPipeServerStreamAcl`, `ProcessStartInfo.ArgumentList`. P/Invoke is ordinary here, not an achievement. |
| brain | Python 3.13 | Pydantic for the args-schema half of the guard, and the LLM/provider ecosystem. The guard's correctness rests on schema validation being boring and well-tested; this is where that is true. |
| ui | TypeScript | CSS/DOM is the largest answer surface for dense information layout, and the approval dialog is the highest-stakes UI in the product. |

**The honest counter-argument, recorded:** a single Rust or C# process could do all of this. What
would be lost is Pydantic-grade schema validation at the guard and the model ecosystem at the
router — both load-bearing. What is gained is one runtime instead of three. If the guard ever turns
out to be simple enough to express without Pydantic, this decision is worth revisiting rather than
defending.

**Not adopted:** LibreHardwareMonitorLib. See §3.

---

## 2. Process and privilege model

**Every process runs `asInvoker`. There is no elevated helper. Nothing in Local Zero prompts for
UAC.**

This is stronger than the original plan, which allotted an elevated helper for sensor access. The
measurements make that helper pointless:

- Everything the panel displays is readable unelevated (M2).
- The one thing elevation might have bought — CPU temperature — is not available at any privilege
  level without a ring-0 driver. The WMI fallback is both denied *and* wrong (M1).

So elevation would buy nothing and cost a permanent high-privilege surface on a personal machine.
The helper is removed and the privilege surface is zero.

### If a future feature genuinely needs admin

It does not get it by elevating the sidecar. The pattern is a separate, minimal helper binary,
launched per action with a UAC prompt, performing exactly one narrow operation and exiting. The
long-running processes stay `asInvoker` for their whole life. Adding such a helper requires a
written entry in `SECURITY.md` first.

The IPC contract encodes this: `hello.payload.elevated` is `const: false`. A sidecar claiming
elevation is a contract violation and the brain refuses the connection rather than trusting it.

### Anti-cheat posture

CS2 runs on this machine under VAC. Binding commitments:

- No kernel driver is ever loaded.
- No reading or writing of another process's memory, for any reason.
- No injection, no hooking, no overlay drawn into another process.
- Process telemetry is limited to what standard Windows process-enumeration APIs report — the same
  information Task Manager shows.
- If process priority control is ever built, it uses `SetPriorityClass` (a documented API, not
  memory manipulation), with a hardcoded anti-cheat exclusion list, explicit per-title opt-in, and
  an audit entry per action.

---

## 3. Sensor strategy

**Driverless, permanently.** No WinRing0, no LibreHardwareMonitor, no ring-0 anything.

### Why LibreHardwareMonitorLib was dropped

The original layer spec mandated it. It is removed, and the reasoning matters because it will be
proposed again:

LHM is a good library, and on a .NET host it loads in-process with no interop layer — a genuine
advantage. But the sensor coverage it offers *beyond driverless sources* is precisely what its
WinRing0 kernel driver provides. Remove the driver and what remains is roughly what Win32 and PDH
already give, wrapped in a dependency. Keep the driver and a ring-0 module is loaded on a machine
running VAC-protected titles, to render a number in a panel.

That is a bad trade, and the driverless decision is not a temporary limitation to be worked around
later. **If the ring-0 decision is ever reversed, this rejection should be revisited deliberately,
not patched around.**

### What is available

| Signal | Source | Admin | Confidence |
|---|---|---|---|
| CPU total and per-core load | PDH `Processor Information`, English API | No | High |
| CPU frequency | Win32 / PDH | No | High |
| RAM used, total, commit | `GlobalMemoryStatusEx` | No | High |
| Uptime | Win32 | No | High |
| GPU utilization per engine | PDH `GPU Engine` | No | **High — measured (M2)** |
| VRAM dedicated usage | PDH `GPU Adapter Memory` | No | **High — measured (M2)** |
| Process list, per-process CPU/RAM | Win32 process enumeration | No | High |
| GPU temperature | AMD ADLX | No | **High — measured (M5)** |
| GPU fan RPM | AMD ADLX | No | Medium — reads, but 0 at idle is a fan-stop, not a sensor |
| GPU board power | AMD ADLX | No | **Unavailable — this card does not report it** |

### What is NOT available

Stated plainly in the UI as *"requires kernel driver — not installed"*. Never estimated, never
interpolated, never inferred from load, never hidden:

- CPU package and per-core temperature — the single most-missed number
- Motherboard / VRM / chipset temperatures
- Chassis and CPU fan RPM
- Per-rail voltages
- CPU package power

> An empty slot labelled "requires kernel driver" is honest. A number that looks like a CPU
> temperature but is actually an ACPI chipset zone is a lie the user will act on.

The contract enforces this structurally: every nullable telemetry field means *unavailable* when
null, and `hello.payload.sensors` declares up front which fields will ever carry a value and why
not. The UI builds its labelled gaps from that declaration, so it never has to guess whether a null
is a missing sensor or a bug.

### Degraded fallback

ADLX was the only uncertain row and the M5 spike settled it, so GPU temperature ships. The
fallback still stands and is now the ordinary path for everyone else: ADLX arrives with the AMD
display driver, so a machine with another vendor's card declares `gpu.temperature_c` unavailable
and keeps load and VRAM, which come from PDH and need no driver. The two sources fail
independently by construction — a separate sensor, a separate fault group — so a missing ADLX
costs one field rather than three. Nothing is reached for to fill the gap.

---

## 4. IPC topology

### system ↔ brain: named pipe, newline-delimited JSON

**Why named pipe over localhost TCP.** A named pipe carries a Windows ACL. `PipeSecurity` can be
set to permit exactly the current user's SID and nothing else, enforced by the OS. A TCP listener
on `127.0.0.1` is reachable by every process running as any user on the machine, and the only
defence available is an application-level token — which has to be stored somewhere, and that
somewhere becomes the new weakest link. For a channel whose messages eventually authorize OS
actions, an OS-enforced ACL is the correct control.

Implementation note that will otherwise cost an afternoon: on .NET Core / 5+ the
`NamedPipeServerStream` constructor does **not** accept `PipeSecurity`. Use
`NamedPipeServerStreamAcl.Create(...)`. Code written from .NET Framework examples will not compile.

**Verified on this machine, 2026-08-11, .NET SDK 10.0.302:**

- `NamedPipeServerStreamAcl.Create(...)` is available **in-box on `net10.0-windows`**. No
  `PackageReference` to `System.IO.Pipes.AccessControl` is needed — an earlier draft of this
  document claimed one was, and that was wrong. Adding it is harmless but unnecessary.
- The .NET Framework style constructor fails to compile, as expected:
  `error CS1729: 'NamedPipeServerStream' does not contain a constructor that takes 8 arguments`.
- A pipe created with a `PipeSecurity` granting only `WindowsIdentity.GetCurrent().User` reports
  exactly **one** access rule, matching that SID. The M1 ACL criterion is therefore reachable as
  specified.

**Why NDJSON over a binary format.** The contract is the load-bearing artifact here, and a
human-readable wire format means a failing message can be read directly out of a log during
debugging. At 1 Hz with a payload this small, encoding cost is not a consideration. If it ever
becomes one, that is a measured bottleneck and §6 applies.

**DECIDED in M1 — the Python end of the pipe is `pywin32`.** Python's standard library has no
Windows named pipe support, and the alternative was `127.0.0.1` TCP with a shared secret, which
would have discarded the ACL argument above. The brain runs one dedicated reader thread feeding an
`asyncio.Queue` via `call_soon_threadsafe`. Full reasoning and evidence in `CONTRACTS.md` §8.

**Verified on this machine, 2026-08-11:** the pipe must be opened with `FILE_FLAG_OVERLAPPED`. A
synchronous `ReadFile` blocks until data arrives and cannot be cancelled by closing the handle from
another thread, which makes the reader thread unstoppable — the test suite hung on shutdown until
the read was changed to wait on its completion event alongside a stop event. Polling with
`PeekNamedPipe` was rejected as the fix because it spends idle CPU forever to solve a problem an
event wait solves for free (§5 of `PERFORMANCE.md`).

### brain ↔ ui: WebSocket

The UI is a browser client; WebSocket is the native fit and needs no justification beyond that.
The brain binds to `127.0.0.1` only.

**DECIDED in M3 — the UI stays an ordinary browser tab.** Whether `ui/` is served to a browser or
wrapped in a desktop shell changes how "the UI has no authority" is enforced: a browser tab gets that
property from the same-origin policy and a strict CSP, while a Tauri or Electron shell would get it
from capability grants and would need its own written justification. Taken by explicit human
decision.

**The accepted cost, recorded because it is a real one.** The approval dialog is the piece most
affected, and a browser tab is the worse host for it: an approval waiting in an unfocused tab is easy
to miss, and the page cannot raise its own window because the browser owns it. Nothing in M3
mitigates that. A desktop shell remains the answer if the miss rate turns out to matter, and M6 is
where it would be revisited — on observed behaviour rather than a preference for a heavier stack.

**M6 did not revisit it, and the reason is that there is nothing to revisit it on.** The trigger was
an observed miss rate, and no dialog has been missed because none has been raised outside tests:
`invoke()` still has no production caller that reaches the approval queue in ordinary use. Rebuilding
the UI as a desktop shell on the strength of an anticipated miss rate would be replacing a measured
decision with a predicted one. The trigger stands and moves to whichever milestone first puts an
approval in front of a user.

### gRPC was considered at M6 and not adopted — DECIDED 2026-08-13

gRPC would bring generated types on both ends and a real streaming model. It also brings protobuf
toolchains in two languages, a build step, and a second contract representation alongside the JSON
Schema that this repo treats as the source of truth.

The condition written at M0 was that migration happens **only if M1–M5 measurements show the JSON
pipe is an actual bottleneck against the budgets in `PERFORMANCE.md`** — not because gRPC is more
professional. The measurements are in, and they do not:

- **P3, the sidecar's own sweep**, p95 **2.00 ms** against a 10 ms budget. This is the work that
  produces a sample, and it is not the transport.
- **P4, sweep completion through the pipe, the brain and the WebSocket to a local consumer**, p95
  **7.07 ms** against a 20 ms budget, re-measured 2026-08-13. The whole delivery path costs under
  1 % of the 1 000 ms it has before the next sample is due.
- **P1/P2 idle working sets** of 59.3 MiB and 51.4 MiB, neither of which is JSON serialisation.

At 1 Hz with one consumer there is no bottleneck for a faster transport to relieve. The cost of
migrating is concrete — two toolchains, a build step, and a second source of truth for a contract
whose single representation is the thing this project keeps its guarantees in — and the benefit is
currently zero milliseconds. **The JSON pipe stays.**

**What would reopen this.** A measured P4 approaching its 20 ms budget, a sample rate materially
above 1 Hz, a payload that grows past what a line of JSON should carry, or a second consumer
process. Any of those is a number; none of them is a preference.

---

## 5. Failure behaviour

Each layer must degrade visibly rather than hang or lie. This is an M1 exit criterion, not a
polish item.

| Failure | Required behaviour |
|---|---|
| system dies while brain is up | Brain emits `system.status {connected:false, reason}`, keeps serving the UI, retries the pipe with backoff. The UI shows a disconnected state and **stops presenting the last sample as live**. |
| brain dies while system is up | Sidecar's pipe write fails; it returns to waiting for a connection. It does not exit, does not spin, does not buffer samples unboundedly — the buffer is bounded and drops oldest. |
| brain dies while ui is up | The socket closes. The UI shows disconnected and reconnects with backoff. It never renders stale numbers without a staleness marker. |
| ui closes | Brain drops the socket and continues; the sidecar keeps sweeping, because the brain's pipe is still open. **Decided in M1:** the sidecar sweeps only while a consumer is attached to its pipe — the tick loop lives per connection, so with nothing connected it costs nothing at all. The brain deliberately does *not* drop the pipe when the last UI tab closes: reconnecting costs a fresh handshake and a PDH warm-up tick, and the measured idle cost of leaving it open is small enough not to buy that back. See §5 of `PERFORMANCE.md` for the number. |
| A message fails schema validation | Dropped, counted, and logged with the offending field. It is never partially applied. The connection is not torn down for a single bad message; repeated violations close it. |
| A sensor read throws | That field becomes null for that sample and an `error` message is emitted once per transition, not once per tick. Other fields in the sample are unaffected. |

The unifying rule: **a layer that cannot do its job says so; it does not substitute a plausible
value and it does not freeze.**

---

## 6. Repository layout

```
E:\Local Zero\
├─ CLAUDE.md                    project invariants — binding on every agent
├─ .gitignore                   written before the first commit
├─ docs\
│  ├─ ARCHITECTURE.md           this file
│  ├─ SECURITY.md               threat model, guard chain, approval flow
│  ├─ CONTRACTS.md              message reference and change procedure
│  ├─ PERFORMANCE.md            budgets and how each is measured
│  └─ ROADMAP.md                M0–M7 with exit criteria
├─ contracts\
│  ├─ ipc.schema.json           system <-> brain, single source of truth
│  ├─ ws.schema.json            brain <-> ui, single source of truth
│  ├─ validate_examples.py      makes the two above mechanically checkable
│  └─ examples\                 valid payloads, plus rejected\ that must fail
├─ system\                      C# .NET sidecar
├─ brain\                       Python FastAPI orchestrator
├─ ui\                          TypeScript / React
├─ bench\                       measurement harness — no number without a script
└─ .claude\                     agent definitions, commands, permission guard
```

---

## 7. Open questions

Carried into M1 rather than guessed at now:

- ~~**Python ↔ named pipe transport**~~ — **closed in M1.** `pywin32` with a reader thread and
  overlapped reads. See §4 and `CONTRACTS.md` §8.
- ~~**The UI's shell**~~ — **closed in M3.** An ordinary browser tab, with the missed-approval cost
  accepted and written into §4.
- **LLM provider set** — which of the intended providers are local (Ollama, already on this machine)
  and which are network calls. This determines whether Local Zero makes outbound connections at all,
  which is a `SECURITY.md` question, not a convenience one. Must be answered before M4.
- ~~**ADLX viability (U1)**~~ — **closed 2026-08-13.** The spike passed in three stages and GPU
  temperature is a measured field; see section 3. Board power is confirmed unavailable on this
  card and is rendered as a gap.
- **WebView2 / browser idle GPU cost (U2)** — Project 0 flagged that a visible webview composites
  regardless of what the page draws. Less acute here since the UI is an ordinary browser tab, but
  the idle budget in `PERFORMANCE.md` is provisional until measured against a blank page.
