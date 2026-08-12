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

Exit criteria, verified 2026-08-12. `brain` suite: **151 passed, 0 skipped**, and still 151 under
`PYTHONOPTIMIZE=1`. `contracts/validate_examples.py` remains 14/14, which is the evidence that M2
touched no contract — the guard is entirely brain-internal and adds no wire message.

- [x] Three example capabilities work, one per `side_effect`: `read`, `write`, `destructive` —
      `read_text_file`, `write_text_file`, `delete_file`. A verdict of "allowed" is not the same as
      working, so each is tested by running its handler from the verdict: the read returns the
      contents, the write creates the file with them, the destructive one removes it
- [x] The guard chain runs in the documented order, and each step has a test proving it denies what
      it is supposed to deny — `test_guard.py`, one section per step, plus
      `test_the_chain_stops_at_the_first_failing_step`. **Step 5 needed a permissive approver stub to
      be reachable at all**: with M2's always-denying approver the origin check could never execute,
      and would have sat untested until M3 made approval succeed
- [x] Path escape attempts are refused, with a test each: `..` traversal, absolute path outside
      `allowed_roots`, symlink crossing the boundary, junction crossing the boundary — all four in
      `test_paths.py`, each a real filesystem construction rather than a representative string. The
      symlink cases need Developer Mode, enabled here 2026-08-12; they skip loudly rather than
      silently passing when it is off, and a junction is never substituted for a symlink
- [x] Containment is checked on the **canonicalised** path, with a test proving
      `<allowed_root>\..\Windows` is refused — and the suite was **mutation-tested** rather than
      merely run. Replacing component containment with a naive string prefix is caught only by the
      sibling-prefix case; removing resolution entirely is caught only by the junction, `..` and
      `<root>\..\Windows` cases. Neither mutation survives, and the two are killed by disjoint sets
      of tests, so none of those four cases is redundant
- [x] `destructive` cannot execute without approval —
      `test_a_destructive_capability_cannot_execute_without_approval` asserts the file is still there
      afterwards. In M2 this holds in the strongest available sense: approving is not yet an action
      anything can take
- [x] Arguments are validated even when the capability name is whitelisted — three tests: an unknown
      field, a wrong type, a missing argument. This is the step the name proves nothing about
- [x] `logs/audit.jsonl` records every decision including denials, with `args_hash` rather than raw
      args — `test_audit.py` asserts a sensitive path never reaches the file and that key ordering
      does not change the hash. Confirmed against the real log end to end
- [x] `/threat-check` reports no CRITICAL or HIGH findings — run 2026-08-12 against the M2 diff. Two
      LOW findings: an `assert` on a guard code path (removed; `python -O` strips asserts) and the
      check-to-use window, accepted with the reasoning recorded in `SECURITY.md` §4 and re-examined
      in M5

**Deferred to M3 by scope, not overlooked:** "a rejected operation is not retried in the same
session" (`SECURITY.md` §5) has no meaning until a rejection can happen, and rejecting is what the
M3 approval flow builds.

---

## M3 — Approval flow and UI

Backend payload → UI dialog → human decision → result.

Exit criteria, verified 2026-08-12. brain **191 passed**, ui **88 passed** with typecheck and build
clean, contracts **22/22**, system 69 untouched.

- [x] The dialog shows the resolved capability, resolved arguments, and the full affected-path list —
      `ApprovalDialog.test.tsx`. An empty `affected_paths` renders as "No files are affected" rather
      than as an absent section: "touches nothing" and "we did not work out what it touches" are
      different facts
- [x] **Markup injected into any payload field is rendered as literal text** — three tests, and the
      injection case reads the checked-in fixture `ws.approval-request-untrusted.json` rather than a
      string written in the test. That file is a *valid* message carrying
      `<img src=x onerror=alert(1)>` and an instruction to approve itself; it is legal on the wire on
      purpose, because filtering it in the contract would be the illusion of cleaning. The tests
      assert the `img` element does not exist and its text *is* visible. A markdown link and a
      `<b>`-wrapped capability name are covered the same way
- [x] `dangerouslySetInnerHTML` appears nowhere in `ui/` — `bans.test.ts`, repository-wide rather
      than approval-path-only, so it cannot be reintroduced anywhere
- [x] No markdown renderer is imported on the approval path — same suite, matching every renderer by
      import
- [x] The `origin` badge works; `untrusted_content` makes the dialog visually distinct and defaults
      the selection to **Reject** — and Reject holds focus for *every* request, not only untrusted
      ones, which is stricter than this asks. A keystroke already in flight when a dialog appears
      should not land on Approve whatever the origin
- [x] Enter is not bound to approve — tested
- [x] The approve control on a `destructive` operation stays disabled for 2 s — tested with fake
      timers. Reject is never delayed: making the safe answer wait would be an argument for the
      dangerous one
- [x] After a rejection the identical invocation is not retried in the same session —
      `test_an_invocation_already_rejected_is_not_queued_again`, keyed on the `args_hash` the audit
      already computes
- [x] `/threat-check` reports no CRITICAL or HIGH findings — run 2026-08-12. One MEDIUM and one LOW,
      both fixed: an approved operation whose handler raised tore down the socket and told the user
      nothing, and it ran synchronously on the event loop. Both closed in the same change, with a
      test. One further MEDIUM is documentation rather than code: approval widens the check-to-use
      window of `SECURITY.md` §4 from microseconds to however long a human takes to answer, and that
      is now recorded there

**Trust mode was added in this milestone at the user's explicit request** and is not an exit
criterion because the ROADMAP predates it. It bypasses approval for every invocation regardless of
`side_effect` or `origin` and persists across restarts, chosen with the consequences stated. What it
does not bypass is the guard — steps 1–3 run in every mode, held by four tests. See `SECURITY.md` §5.

**Not demonstrated in a browser.** Nothing can raise a real dialog yet: the UI cannot construct an
invocation by contract, and the planner that will is M4. `BrainServices.invoke()` is the entry point
and only tests call it. Every criterion above is met by test rather than by clicking, and that is
stated rather than glossed.

---

## M4 — LLM layer

Multi-provider abstraction behind one interface. **This is where the injection surface opens.**
Everything in M2 and M3 exists so that this milestone changes one thing at a time.

**The question that blocked this milestone is answered.** Which providers are local and which are
network calls is settled in `SECURITY.md` §11: **Selectable Hybrid** — Local (Ollama, loopback only)
is the default on a fresh install, Cloud (Gemini) is opt-in, and the key lives in the Windows
Credential Manager. Read §11 before touching this milestone; it states deliberately which of its
three enforcement mechanisms is a hard guarantee and which two are not.

Built in three slices, so the injection surface opens one piece at a time:

| Slice | Contents |
|---|---|
| **M4a** | The egress guard, the provider interface, `Secret`, Credential Manager |
| **M4b** | The planner/reader split, the two chunk types, the injection test set |
| **M4c** | Provider selection and key entry in the UI — three additive WebSocket messages |

Exit criteria:

- [ ] Providers work through a single interface; keys come from the environment or Credential
      Manager, never from source
- [ ] **No non-loopback egress in Local mode**, enforced process-wide against any library rather
      than by convention, and every departure in Cloud mode recorded in the audit log
- [ ] Embeddings are computed locally in **both** modes — indexing the vault through a network
      provider would send its contents off the machine one chunk at a time
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

## M4.5 — Memory

Long-term memory in an Obsidian vault, with a local semantic index over it. Sequenced after M4
because classification, conflict detection and consolidation all need a model, and placed before M5
because a capability that can act is more dangerous once something can remember what to do.

**The vault is the trusted namespace, and that is a decision with one sharp edge.** `SECURITY.md` §2
defines trusted memory as the user's own notes, which is exactly what a vault is — but only while a
human is the one writing it. The moment Local Zero writes a note that is later retrieved as trusted,
the model can author its own instructions and read them back next session, which is the structural
break in §2 undone from the inside.

So trust follows **who wrote it**, recorded in each note's frontmatter, not where the file sits:

| Location | Trust | Written by |
|---|---|---|
| `Memory/`, `Projects/`, `Knowledge/`, `System/` | trusted | the user, by hand |
| `Memory/LocalZero/`, `Conversations/` | **untrusted** | Local Zero, `source: agent` |
| `Archive/` | untrusted | the forgetting path |

Promotion from agent-written to trusted is a human moving a file. There is no API for it.

Exit criteria:

- [ ] A trusted note reaches the planner; an `UntrustedChunk` provably cannot — the type has no
      conversion to `TrustedChunk` and a test asserts it
- [ ] Agent-written memory lands in `Memory/LocalZero/` and is retrieved as untrusted, tested with
      an injection fixture that reaches the Reader and produces no invocation
- [ ] Vault writes go through **registered capabilities** with `allowed_roots` limited to
      `Memory/LocalZero/` and `Archive/`; every other vault path is on the guard's protected list,
      with a test per path
- [ ] Forgetting archives by default; permanent deletion is `destructive` and needs its own approval
- [ ] Embeddings never leave the machine in either mode — test
- [ ] Incremental reindex touches only changed files, **measured** rather than asserted
- [ ] Missing vault, corrupt frontmatter, absent Ollama and a corrupt index each degrade rather than
      crash: telemetry and approval keep working with memory switched off
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
