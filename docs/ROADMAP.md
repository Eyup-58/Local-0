# Local Zero — Roadmap

**The rule: no milestone begins until the previous one passes `/gate Mx`.**

`/gate` reads the exit criteria below and verifies each one individually, with evidence. A criterion
without evidence is `DOĞRULANAMADI` (unverifiable), never PASS. Nothing here is self-certified.

Each milestone gets its own planning session. Do not plan M1–M6 in one pass: the contract and the
budgets will change once M1 produces real measurements, and later plans must be written against
those, not against today's guesses.

---

## M0 — Contracts and threat model

**No implementation code.** Output is design decisions and machine-checkable contracts.

Exit criteria:

- [ ] `docs/ARCHITECTURE.md`, `SECURITY.md`, `CONTRACTS.md`, `PERFORMANCE.md`, `ROADMAP.md` all exist
- [ ] `contracts/ipc.schema.json` and `contracts/ws.schema.json` are valid JSON Schema draft 2020-12
- [ ] Every example payload validates, and every `rejected/` example fails **for its intended
      reason** (the validator prints the offending field, not a generic oneOf failure):
      `uv run --with jsonschema python contracts/validate_examples.py` → all expectations hold
- [ ] `SECURITY.md` contains a written threat model for the ingest → memory → planner → executor
      chain, naming where the chain structurally breaks
- [ ] Capability registry fields and the guard chain order are defined, with what each step blocks
- [ ] The privilege model is documented: every process `asInvoker`, no elevated helper, with the
      measurement that makes elevation pointless
- [ ] Performance budgets are written as numbers, each naming the script that will produce it, and
      each labelled provisional until measured
- [ ] `.gitignore` covers secrets, `logs/`, `audit.jsonl`, and build output — **before** the first
      commit
- [ ] `.claude/` scaffolding present: `settings.json`, three subagents, three commands, and the
      contract guard hook the settings file references
- [ ] `CLAUDE.md` carries the red lines plus the two machine-specific invariants (L1 localized
      counters, ring-0 ban)

**Not gated on:** `dotnet build`, `pytest`, `npm run build`. There is no code to build.

---

## M1 — Vertical slice: telemetry end to end

The first thing that runs. C# sensors → named pipe → Python → WebSocket → live CPU/GPU in the UI.

**Prerequisite: met.** .NET SDK 10.0.302 installed 2026-08-11. `NamedPipeServerStreamAcl` verified
available in-box on `net10.0-windows`, and a pipe built with an explicit `PipeSecurity` was
confirmed to report exactly one access rule matching the current user's SID — so the ACL criterion
below is known reachable rather than hoped for.

**In scope:** sensor reading, pipe server with explicit ACL, pipe client, FastAPI + WebSocket
broadcast, UI client with a live view, bench scripts for idle RSS and poll latency.

**Out of scope — do not write in this milestone:** LLM integration, capability registry, the guard,
the approval flow, game scanning, OS operations.

Exit criteria, with the evidence for each. Verified 2026-08-11.

- [x] All three processes start and the UI shows live data at 1 Hz — run together against live
      hardware; the panel reported `Live`, seq advancing, 28 core cells, 0 frames refused
- [x] **Every process runs `asInvoker`; no UAC prompt appears at any point** — `app.manifest`
      requests `asInvoker`, `ElevationGuard` refuses to start elevated at runtime, and
      `PrivilegeTests` asserts both the declaration and that the suite itself is unelevated
- [x] **CPU counters read correctly under this machine's Turkish locale**, via
      `PdhAddEnglishCounterW`, with a test that fails if a localized counter path is introduced —
      `BuildInvariantTests`. Proven to fail: a deliberate violation was introduced and the gate
      caught it before being removed. Measured directly: the English API returned 15.59 where the
      localized API returned `0xC0000BB8` for the identical path
- [x] `System.Diagnostics.PerformanceCounter` appears nowhere in `system/` — 0 occurrences in code.
      The only two matches in the tree are inside doc comments explaining the ban, which the gate
      excludes by skipping comment lines
- [x] Named pipe ACL grants the current user's SID only, verified by a test, not by inspection —
      `PipeSecurityTests` asserts exactly one access rule matching the current SID, and separately
      that neither Administrators nor SYSTEM appear
- [x] Schema-invalid messages are dropped and counted, with a test on each boundary —
      `InboundMessageParserTests` (system), `test_session.py` (brain), `guards.test.ts` (ui).
      Exercised live too: an unknown field and `v: 99` were both refused over a real pipe while the
      connection stayed up
- [x] Unavailable sensors arrive as null with a populated `unavailable_reason`, and the UI renders a
      labelled gap rather than a zero — `rendering.test.tsx` asserts the reason is shown and that no
      zero appears; confirmed on screen for `cpu.temperature_c` and `gpu.temperature_c`
- [x] Killing any one layer leaves the other two in a visible error state — no hang, no freeze, and
      **no stale sample presented as live** — the sidecar was killed under a live browser session:
      the last reading stayed on screen, the verdict flipped to `Not live`, and the sidecar's own
      prose explained why. Socket loss is covered by `reducer.test.ts`
- [x] `/bench` runs and produces real numbers; `docs/PERFORMANCE.md` §5 is filled in with them and
      the provisional budgets are revised against measurement — all five budgets measured, P2, P3
      and P5 revised. §5 also records what the numbers do **not** cover
- [x] `/threat-check` reports no CRITICAL or HIGH findings — run 2026-08-12 against `SECURITY.md`,
      all ten items. Clean on the six that apply: no process-launching or `eval` construct exists in
      any layer (0 matches across `system/`, `brain/`, `ui/`), the pipe ACL adds exactly one rule for
      the current SID with `MaxServerInstances = 1`, every process is `asInvoker` with a runtime
      guard, the brain binds `127.0.0.1` with no CORS middleware, no secret appears in first-party
      code, and no unavailable sensor is rendered as a zero. Four items are not applicable in M1
      because no capability registry, path argument, approval flow or invocation exists yet — and
      the absence is enforced rather than incidental: a `capability.invoke` frame is refused at both
      boundaries (`InboundMessageParserTests.cs`, `guards.test.ts`).

      **One MEDIUM, closed in M1:** `SECURITY.md` §7 named a high-entropy pre-commit hook as half of
      its defence against a committed credential, and no such hook existed — `.git/hooks` held only
      samples and `core.hooksPath` was unset. `.githooks/pre-commit` now implements it, verified
      against eight cases including the exact `github.txt` shape that prompted it

**Contract amendment during M1.** `per_core_percent` entries were widened to `number | null` so a
parked core can hold its slot. Taken by explicit human decision and recorded in `CONTRACTS.md` §5.

---

## M2 — Capability registry and guard (still no LLM)

The guard is built and tested against hardcoded invocations. **Deliberately before the LLM exists**
— if the guard is proven correct while nothing can talk to it, then when M4 adds a model, any
failure is isolated to the model layer rather than ambiguous.

Exit criteria:

- [ ] Three example capabilities work, one per `side_effect`: `read`, `write`, `destructive`
- [ ] The guard chain runs in the documented order, and each step has a test proving it denies what
      it is supposed to deny
- [ ] Path escape attempts are refused, with a test each: `..` traversal, absolute path outside
      `allowed_roots`, symlink crossing the boundary, junction crossing the boundary
- [ ] Containment is checked on the **canonicalised** path, with a test proving
      `<allowed_root>\..\Windows` is refused (prefix-matching the raw string would accept it)
- [ ] `destructive` cannot execute without approval — test
- [ ] Arguments are validated even when the capability name is whitelisted — test
- [ ] `logs/audit.jsonl` records every decision including denials, with `args_hash` rather than raw
      args
- [ ] `/threat-check` reports no CRITICAL or HIGH findings

---

## M3 — Approval flow and UI

Backend payload → UI dialog → human decision → result.

Exit criteria:

- [ ] The dialog shows the resolved capability, resolved arguments, and the full affected-path list
- [ ] **Markup injected into any payload field is rendered as literal text.** Test: place
      `<img src=x onerror=...>` and `[click](javascript:...)` into `resolved_args` and assert the
      DOM contains text, not elements
- [ ] `dangerouslySetInnerHTML` appears nowhere in `ui/` (grep is evidence)
- [ ] No markdown renderer is imported on the approval path
- [ ] The `origin` badge works; `untrusted_content` makes the dialog visually distinct and defaults
      the selection to **Reject**
- [ ] Enter is not bound to approve
- [ ] The approve control on a `destructive` operation stays disabled for 2 s
- [ ] After a rejection the identical invocation is not retried in the same session — test
- [ ] `/threat-check` reports no CRITICAL or HIGH findings

---

## M4 — LLM layer

Multi-provider abstraction behind one interface. **This is where the injection surface opens.**
Everything in M2 and M3 exists so that this milestone changes one thing at a time.

**Blocked on an open question:** which providers are local (Ollama is already on this machine) and
which are network calls. That answer determines whether Local Zero makes outbound connections at
all, and it must be settled in `SECURITY.md` before code is written.

Exit criteria:

- [ ] Providers work through a single interface; keys come from the environment or Credential
      Manager, never from source
- [ ] A missing key is a startup failure with a clear message, not a runtime surprise
- [ ] No key appears in any log, error message, test fixture, or commit — a `Secret` wrapper makes
      accidental interpolation print `[redacted]`
- [ ] Malformed structured output is handled gracefully with a bounded retry — no infinite loop
- [ ] **The injection test set passes:** instructions embedded in file contents, in a stored memory
      record, and in a telemetry string field do not become capability invocations
- [ ] The Reader path has an empty capability registry, asserted by a test
- [ ] Trusted and untrusted retrieval have separate return types with no conversion between them,
      asserted by a test
- [ ] `/threat-check` reports clean

---

## M5 — OS operations and game scanning

Real capabilities. Each one enters through the registry; none is special-cased.

Exit criteria:

- [ ] Every new capability is registered with all five fields and has tests for its guard behaviour
- [ ] Game library detection is strictly read-only
- [ ] The AMD ADLX spike (U1) is timeboxed: read one temperature value from C#, then stop. **If it
      fails, GPU temperature is cut** and the panel ships with load and VRAM. Nothing else may
      depend on ADLX until this passes.
- [ ] No capability reads or writes another process's memory
- [ ] `/threat-check` run **on every capability addition**, not only at the milestone boundary

---

## M6 — Hardening (gRPC optional)

Fault injection, RAM and latency profiling, and a possible transport migration.

Exit criteria:

- [ ] Fault injection: each layer killed at each stage, recovery verified against §5 of
      `ARCHITECTURE.md`
- [ ] Long-run profile: no unbounded memory growth over an extended session
- [ ] **The gRPC migration happens only if a measured bottleneck justifies it.** If the numbers do
      not, this milestone records that decision and skips the migration. "More professional" is not
      a reason.

---

## Command reference

| Command | Purpose |
|---|---|
| `/gate Mx` | Verify a milestone's exit criteria with evidence. Reports only; fixes nothing. |
| `/threat-check` | Project-specific trust-boundary audit against `SECURITY.md`. Reports only. |
| `/bench` | Run `bench/` scripts and report against `PERFORMANCE.md`. Measured numbers only. |
| `/code-review ultra` | General code-quality sweep. **User-triggered and separately billed** — the agent cannot launch it. |

`/threat-check` and `/code-review ultra` do not substitute for each other. The general reviewer does
not know this project's threat model; the threat-check does not look for ordinary bugs.

**Run `/threat-check` at every expansion of the LLM or OS surface, not only at milestone
boundaries.**
