# Local Zero — Roadmap

**The rule: no milestone begins until the previous one passes `/gate Mx`.**

`/gate` reads the exit criteria below and verifies each one individually, with evidence. A criterion
without evidence is `DOĞRULANAMADI` (unverifiable), never PASS. Nothing here is self-certified.

Each milestone gets its own planning session. Do not plan M1–M7 in one pass: the contract and the
budgets will change once M1 produces real measurements, and later plans must be written against
those, not against today's guesses.

---

## M0 — Contracts and threat model

**No implementation code.** Output is design decisions and machine-checkable contracts.

Exit criteria, with the evidence for each. **Gated retroactively on 2026-08-13** — M0 shipped before
`/gate` was in the habit, and M1–M3 were ticked while this section was not. Every criterion below was
re-checked against the tree as it stands, not from memory.

- [x] `docs/ARCHITECTURE.md`, `SECURITY.md`, `CONTRACTS.md`, `PERFORMANCE.md`, `ROADMAP.md` all exist
      — all five present in `docs/`
- [x] `contracts/ipc.schema.json` and `contracts/ws.schema.json` are valid JSON Schema draft 2020-12
      — both pass `Draft202012Validator.check_schema`, and both declare
      `$schema: .../draft/2020-12/schema` rather than merely being accepted by a lenient validator
- [x] Every example payload validates, and every `rejected/` example fails **for its intended
      reason** (the validator prints the offending field, not a generic oneOf failure):
      `uv run --with jsonschema python contracts/validate_examples.py` → all expectations hold —
      `37/37 expectations held`, grown from 14/14 at M2 as the contract took on the approval,
      provider and turn messages
- [x] `SECURITY.md` contains a written threat model for the ingest → memory → planner → executor
      chain, naming where the chain structurally breaks — §1 names the attacker as anyone
      controlling text the system ingests, §2 is titled "The structural break" and marks
      `READER ← THE BREAK POINT` in the diagram, and §2 states it in one sentence: the component
      that can invoke capabilities never receives untrusted text
- [x] Capability registry fields and the guard chain order are defined, with what each step blocks —
      `SECURITY.md` §4: "A capability does not exist unless it is registered with all five fields",
      then "The guard chain — order is invariant" with what each of the five steps refuses
- [x] The privilege model is documented: every process `asInvoker`, no elevated helper, with the
      measurement that makes elevation pointless — `ARCHITECTURE.md` §2: GPU counters return live
      data unelevated, and CPU temperature is unavailable at any privilege level on this board, so
      elevation buys a number that is wrong. All three processes marked `asInvoker` in the diagram
- [x] Performance budgets are written as numbers, each naming the script that will produce it, and
      each labelled provisional until measured — `PERFORMANCE.md` §3, P1–P5, each naming its
      `bench/` script. The provisional labels are gone because M1 measured all five and revised
      three; §5 holds the dated results, which is the state this criterion was aiming at
- [x] `.gitignore` covers secrets, `logs/`, `audit.jsonl`, and build output — **before** the first
      commit — checked against the first commit itself rather than today's file
      (`git show <first>:.gitignore`): `.env`, `.env.*`, `!.env.example`, `/logs`, `audit.jsonl`,
      `*.log`, `.venv/`, `node_modules/` were all present in it
- [x] `.claude/` scaffolding present: `settings.json`, three subagents, three commands, and the
      contract guard hook the settings file references — `agents/` holds `brain-python`,
      `system-csharp`, `ui-typescript`; `commands/` holds `bench`, `gate`, `threat-check`; and
      `hooks/guard_contracts.py` exists, which is the path `settings.json` names in its
      `PreToolUse` matcher
- [x] `CLAUDE.md` carries the red lines plus the two machine-specific invariants (L1 localized
      counters, ring-0 ban) — 14 numbered red lines, including 9 (no localized performance
      counters, `PdhAddEnglishCounterW` required) and 10 (no kernel drivers, unavailable telemetry
      labelled rather than estimated)

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

Exit criteria, with the evidence for each. **Gated on 2026-08-13**, after M4.5 had already been
built on top of it — the rule at the head of this file was not followed here, and the gate was run
late rather than skipped. `brain` suite: **454 passed**; the M4 layer's own files
(`test_llm`, `test_egress`, `test_credentials`, `test_provider_ws`) account for **57**.

- [x] Providers work through a single interface; keys come from the environment or Credential
      Manager, never from source — `Provider` is a `Protocol`, and neither planner nor reader knows
      which implementation it holds. The key is read only through `win32cred.CredRead`, stored
      `CRED_PERSIST_LOCAL_MACHINE` so it does not roam. A pattern scan for Google and OpenAI key
      shapes across `brain/`, `ui/src`, `system/` and `docs/` returns nothing
- [x] **No non-loopback egress in Local mode**, enforced process-wide against any library rather
      than by convention, and every departure in Cloud mode recorded in the audit log — 19 tests in
      `test_egress.py`. The guard patches the socket itself, so `test_the_guard_is_not_routed_around_by_a_higher_level_helper`
      and `test_connect_ex_is_guarded_too` hold for any library; datagrams are refused with and
      without flags; IPv6 loopback and `localhost` by name both resolve as loopback and a hostname
      that will not resolve is refused. `test_cloud_mode_records_every_departure` covers the audit
      half, and `test_loopback_traffic_is_not_recorded` keeps the log meaningful
- [x] Embeddings are computed locally in **both** modes — indexing the vault through a network
      provider would send its contents off the machine one chunk at a time —
      `TestEmbeddingsNeverLeaveTheMachine`, run in Cloud mode with egress permitted; every address
      reached was `127.0.0.1`. `GeminiProvider.embed` raises rather than exporting the vault, and
      `create_app` wires `OllamaProvider()` as the embedding provider whatever the selected mode
- [x] A missing key is a startup failure with a clear message, not a runtime surprise —
      `MissingKey` is its own error type and `test_cloud_mode_without_a_key_fails_at_construction`
      proves it fires when the provider is built, not deep in a call. Local mode needs no key, and
      the UI is told `has_key` during the handshake, so the state is visible before Cloud is chosen
- [x] No key appears in any log, error message, test fixture, or commit — a `Secret` wrapper makes
      accidental interpolation print `[redacted]` — `repr`, `str`, f-string, `%` interpolation, a
      log record, and containment in a list are each asserted separately, and
      `test_a_secret_is_not_json_serializable` closes the most likely leak of all. `reveal()` is
      called in exactly two places: writing to Credential Manager and setting the request header.
      The commit half is M1's `.githooks/pre-commit`, with `core.hooksPath` set to `.githooks`
- [x] Malformed structured output is handled gracefully with a bounded retry — no infinite loop —
      `MAX_STRUCTURED_ATTEMPTS = 3`, named rather than inlined so the test asserting termination
      uses the same number the loop does. A retry that succeeds returns the parsed object, and a
      fenced code block is still read as JSON rather than counting as malformed
- [x] **The injection test set passes:** instructions embedded in file contents, in a stored memory
      record, and in a telemetry string field do not become capability invocations —
      `TestTheThreeIngestPaths`, one case each, all carrying the same `delete_file` on
      `config\SAM` payload into a provider that obeys any instruction it is shown
- [x] The Reader path has an empty capability registry, asserted by a test —
      `test_its_registry_is_empty`, and the stronger `test_a_registry_cannot_be_handed_to_it`:
      there is no parameter to fill, so it is not "empty by convention"
- [x] Trusted and untrusted retrieval have separate return types with no conversion between them,
      asserted by a test — `TestTheTwoTypes`. The field names differ deliberately, so the
      conversion somebody writes in a hurry raises rather than succeeding quietly
- [x] `/threat-check` reports clean — run 2026-08-13 over the M4 surface, all ten items. The two
      worth naming: the Gemini endpoint is a pinned constant with the model from a constant, never
      assembled from input, and a search for `shell=True`, `subprocess`, `os.system`, `eval(` and
      `exec(` across the LLM and network layers returns zero. Items 5, 9 and 10 are unchanged by
      M4, which touches neither `system/` nor the telemetry path

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
| `LocalZero/`, `Conversations/` | **untrusted** | Local Zero, `source: agent` |
| `Archive/` | untrusted | the forgetting path |

Promotion from agent-written to trusted is a human moving a file. There is no API for it.

Exit criteria, with the evidence for each. Verified 2026-08-13.

- [x] A trusted note reaches the planner; an `UntrustedChunk` provably cannot — the type has no
      conversion to `TrustedChunk` and a test asserts it — `test_injection.py::TestTheTwoTypes`. The
      two types carry different field names on purpose, so `TrustedChunk(**vars(untrusted))` raises
      rather than converting; `TestThePlanner` refuses a mixed list whole, one untrusted item in a
      hundred trusted ones being still the exploit. The other half is asserted too:
      `test_trusted_memory_reaches_the_planner_and_shapes_the_prompt`
- [x] Agent-written memory lands in `LocalZero/` and is retrieved as untrusted, tested with
      an injection fixture that reaches the Reader and produces no invocation — `TestTheRoundTrip`
      and `test_a_poisoned_note_produces_no_invocation_through_the_guard`. The fixture note demands
      `delete_file` on `config\SAM` and the stub provider obeys any instruction it is shown, so the
      test would produce that invocation if untrusted text could reach the planner
- [x] Vault writes go through **registered capabilities** with `allowed_roots` limited to
      `LocalZero/` and `Archive/`; every other vault path is on the guard's protected list,
      with a test per path — `handlers.py:158-180` and `ws/server.py:205`. Every trusted folder is
      parametrised at all three defences: containment, the protected list, and the subtree beneath
      it. `TestTheProductionWiring` asks the application's own guard, so a list narrowed in
      `create_app` cannot pass tests written against a hand-built one. Fixed in `acc0ba3`
- [x] Forgetting archives by default; permanent deletion is `destructive` and needs its own approval
      — `TestArchiving`, `TestForgetting`. `memory_forget` reaches only what is already archived, so
      "forget that" cannot be unrecoverable on the first try, and it returns `Pending` with
      `side_effect == "destructive"`
- [x] Embeddings never leave the machine in either mode — test —
      `TestEmbeddingsNeverLeaveTheMachine`, run in **Cloud** mode with egress permitted rather than
      in Local mode where the guard would be the thing under test. Every address the embedding path
      reached was `127.0.0.1`, and `GeminiProvider.embed` raises rather than exporting the vault
- [x] Incremental reindex touches only changed files, **measured** rather than asserted —
      `bench/reindex_incremental.py`, 2026-08-13: a 40-note vault, one note edited, warm pass
      1 indexed / 39 skipped, 1.985 s → 0.052 s with embeddings on. Wall time rather than the
      counter, because a counter reading one proves the counter, not that the second scan is cheaper
- [x] Missing vault, corrupt frontmatter, absent Ollama and a corrupt index each degrade rather than
      crash: telemetry and approval keep working with memory switched off — `TestWithoutAVault`,
      `TestFrontmatter`, `test_a_provider_with_no_embedding_model_degrades_to_keyword_search`, and
      `TestACorruptIndex` (10 cases) and `TestAnIndexThatIsNotCorrupt` (3), with the handshake case
      in `test_ws_server.py`. Fixed in `bfb3791`, and narrowed after review — see below
- [x] `/threat-check` reports clean — run 2026-08-13 over the memory surface, all ten items covered,
      no findings. Path canonicalisation is exercised against a real junction and a real symlink in
      `test_paths.py` rather than argued for

**2026-08-13 — the gate failed twice before it passed, both times on something absent rather than
wrong.** `/gate M4.5` on 2026-08-12 returned FAIL on two criteria, and neither was visible from
reading the code that implemented them.

`MemoryIndex` never caught `sqlite3.DatabaseError`, and `status()` is called during the WebSocket
handshake — before the frame reporting the turn state. So a corrupt cache did not degrade memory, it
dropped the connection, and the tab reconnected into the same failure. Telemetry and approval use
memory for nothing and went down with it. The fix is the rule this file already applied to a stale
schema: the vault is the source of truth, so a cache that will not open is discarded and rebuilt
rather than repaired.

**The repair had its own Windows-shaped bug, found by testing it rather than by reading it.**
Discarding the file from under a live connection cannot work — Windows will not unlink an open
file — so corruption surfacing mid-query turned into a permanent memory-off on the only platform
this product runs on. Closing before discarding is one line, and nothing but a test that provoked
the case would have shown it was missing.

**The recovery was then too eager, which review caught and the tests had not.** `sqlite3` raises
`DatabaseError` itself only for a damaged file and a *subclass* for everything else, so catching the
base class swept in `database is locked` — which this product generates on its own, since `reindex`
runs on a worker thread while the handshake calls `status()` on the event loop. Reproduced on this
machine: a five-second busy timeout was read as corruption, the unlink then failed with `WinError 32`
because the writer still held the file, and memory was off for the rest of the session over a lock
that cleared a moment later. A mistyped column did the same thing more quietly — deleted and rebuilt
on every call, so a query bug read as an empty vault. Both now answer empty for the one call and
leave the file alone; only `SQLITE_NOTADB` and `SQLITE_CORRUPT` reach the discard. Two further gaps
closed with it: a discard now retries the work that provoked it, so a user's rescan indexes the vault
instead of reporting zero, and an `OSError` from an uncreatable directory degrades instead of raising
through `status()` into the handshake.

**Narrowing the catch stopped the data loss and left the stall, which only measurement showed.** A
lock no longer deletes anything, but the reader still waited for it, and `_memory_frame` calls
`status()` on the event loop — so what the reader waits for, every connected tab waits for. Measured
with a 300-note vault: the scan took 1.46 s and one concurrent `status()` blocked 1.57 s, the whole
scan, because a writer whose page cache spills upgrades to EXCLUSIVE and holds it to commit. Past a
five-second scan it would have reported zero notes as well. The index now opens in **WAL**, where a
reader takes the last committed snapshot and never waits: worst case over the same scan fell to
0.05 s, and the scan itself to 0.61 s. `_discard` removes the `-wal` and `-shm` sidecars with the
file, so a rebuilt index cannot be read through a log written against the corrupt one.

The second failure was smaller and worse. `Projects`, `Knowledge` and `System` were protected in
`create_app` and had been all along; what was missing was any test that said so. `Memory` was
asserted, the other three were inherited by a fixture that built one folder, and a list that lost
them would have stayed green — the whole reason the criterion asks for a test *per path*.

**2026-08-12 — the panel was rebuilt as an orchestration centre.** Design imported from
`claude.ai/design`; shipped in `f91f3ac` (contract) and `ceab545` (implementation).

It sits in M4.5 rather than M5 because it adds no capability. What it adds is a way to *report* the
ones that already exist: two brain → ui messages, `turn.state` and `tool.log` (`CONTRACTS.md` §4),
so the panel can show what the brain is doing and what it ran without inferring either from elapsed
time. The design as delivered assumed it could infer them — its demo sequence advanced turns nobody
reported, its telemetry strip drew load from `Math.random()`, and its caption typed itself out one
character at a time. None of that was ported: each invents a value, which is the one thing invariant
10 exists to prevent. Extending the contract was the alternative to faking it, and that is the whole
reason this slice touched `contracts/` at all.

**The core stayed Canvas 2D.** The mockup drew it as a Three.js shader over ~11k points. This panel
reports GPU utilization, so a WebGL loop behind that number would inflate the figure it draws — the
one thing from the design that could not be taken at any price.

- [x] Both message types are outbound only, absent from `ClientMessage`: a tab cannot assert a turn
      that never happened or a tool call that never ran — `brain/tests/test_turn_state.py`
- [x] A null caption renders as nothing; empty prose is refused at the contract so silence has one
      spelling — `rejected/ws.turn-state-empty-caption.json`
- [x] Fonts vendored to `ui/public/fonts`, so the panel renders identically with no network at all
      and `bans.test.ts` still refuses a CDN reference
- [x] 35/35 contract expectations, 381 brain, 155 ui; driven in Chromium against contract-shaped
      frames with no console errors

**2026-08-12, same day — `thinking` and `speaking` wired, `listening` deliberately not.** Adding
`turn.request` (ui → brain) gave the planner its first caller outside tests: `BrainServices` now
holds a `Planner`, and a request drives `thinking` while it runs, then either a proposal through the
existing guard and approval gate or `speaking` carrying the model's own words.

`propose()` used to return `Invocation | None` and threw the model's reason away. That reason *is*
what the brain says when it declines to name a capability, so it now returns a `Proposal` carrying
both. The old signature could not have been wired honestly — there was nothing to speak.

- [x] `thinking` is emitted while the planner runs, and the call is off the event loop so telemetry
      keeps flowing during it
- [x] `speaking` carries the model's own reason, never a template the panel wrote
- [x] A proposal returns the turn to `idle` and lets the approval gate speak for itself, rather than
      narrating over a dialog the user is already reading

**`listening` stays unwired, on purpose.** There is no microphone, no speech capture and no STT
anywhere in this project, so nothing can report that state without inventing it — and inventing it
is the exact failure this slice was built to avoid. It stays in the enum and the UI renders it,
because narrowing an enum later is a breaking change. It starts being sent when voice input exists,
which is M5/M6 work, and not before.

---

## M5 — OS operations and game scanning

Real capabilities. Each one enters through the registry; none is special-cased.

Exit criteria, with the evidence for each. Verified 2026-08-13. `516` brain, `76` C#, `178` ui,
contracts `39/39`.

- [x] Every new capability is registered with all five fields and has tests for its guard behaviour
      — four added: `list_processes` (read), `open_folder` (write), `scan_games` (read),
      `launch_application` (destructive). 30 cases in `test_os_capabilities.py` cover containment,
      canonicalisation, traversal, an undeclared argument, and approval routing per side effect.
      Two declare no roots, and that is now a statement rather than an omission — see below
- [x] Game library detection is strictly read-only — structural rather than promised:
      `capabilities/steam.py` contains no write path at all, only `read_text` and a narrow
      `glob("appmanifest_*.acf")`. Run against the real install at `d:\steam`: Counter-Strike 2 at
      68301 MB and the Steamworks redistributables, matching what is on disk
- [x] The AMD ADLX spike (U1) is timeboxed: read one temperature value from C#, then stop. **If it
      fails, GPU temperature is cut** and the panel ships with load and VRAM. Nothing else may
      depend on ADLX until this passes. — three go/no-go stages, all passed, and the vtable layout
      was proved before any value was believed: `GPUVRAM` returned **2942 MB against PDH's 2942 MB**
      in the same second. Verified end to end rather than in a unit test - sidecar → pipe → brain →
      WebSocket delivered `gpu.temperature_c=48` while `cpu.temperature_c` stayed null. The
      no-dependency rule holds structurally: the temperature is its own sensor in its own fault
      group, so a machine without ADLX keeps PDH's utilization and VRAM
- [x] No capability reads or writes another process's memory — `list_processes` calls
      `psutil.process_iter(["name", "pid", "cpu_percent", "memory_info"])` and nothing else.
      `memory_info` is the working-set counter Windows already publishes; no handle is opened to
      another process's address space, and `ReadProcessMemory` appears nowhere in the repository
- [x] `/threat-check` run **on every capability addition**, not only at the milestone boundary —
      run three times, and it **found something**: `open_folder`'s first draft launched
      `explorer.exe` by bare name, which CreateProcess resolves by searching, so a file of that name
      earlier in the search order would decide what ran. Now an absolute path under `%SystemRoot%`
      with three tests holding it. The finding existed while the capability was being written; at
      the milestone boundary it would have been three commits deep

**2026-08-13 — two things this milestone changed that were not on the list.**

`allowed_roots` was required of every capability, always, which reads as the stricter rule and is
not. A capability with no path argument has nothing for step 3 to resolve, so any root it declared
was never consulted — and handing `list_processes` the workspace to satisfy the constructor would
have written down a containment claim no code enforces. A control that is decoration is worse than
an absent one, because a reader counts it. The requirement is now conditional and runs both ways.

**A capability the guard allowed outright never ran.** `_execute` had one production caller, the
approval path, so an `Allowed` verdict fell through to an idle turn with the handler never called —
while the comment above that line asserted the opposite. Shipped behaviour since M2:
`read_text_file` on a workspace path did nothing, silently. Found while tracing where a read
capability's result would go, which is also what produced `capability.result`: `tool.log` can say a
capability finished, and for a process list that is the one thing nobody asked.

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

## M7 — Packaging

**Added 2026-08-13, after M5.** Until this exists the only person who can run Local Zero is the
person who built it: three processes started by hand, the UI on a Vite dev server, and a reader of
the repository left to work out the order. For something about to be open-sourced that is not a
finishing touch, it is the difference between a project and a program.

Two concrete gaps, measured rather than assumed:

- **Nothing serves the built UI.** `ui/dist` is produced by `npm run build` and no process hosts it;
  the brain mounts no static files. The dev server is doing that job today.
- **The UI hardcodes `ws://127.0.0.1:8765/ws`** (`ui/src/ws/useTelemetry.ts`). Loopback is correct
  and stays; the port being a literal in the client is what needs deciding.

What is **not** a gap, checked before this was written: no machine-specific path exists in
production code, and the only environment variables read are `LOCALAPPDATA`, `SystemRoot`, the three
`ProgramFiles` forms, and `OBSIDIAN_VAULT_PATH`. Steam is discovered from its own registry key.
Nothing about this build assumes it is this machine.

Exit criteria:

- [ ] One command starts all three layers, and stopping it stops all three. No step of the current
      three-terminal sequence survives as something a user is expected to know.
- [ ] The UI ships as built assets with no dev server, and the port the client connects to is
      configured in one place rather than being a literal in two.
- [ ] **Every process still runs `asInvoker` and the install needs no elevation.** An installer that
      asks for administrator would break red line 11 to deliver a convenience, and the whole
      privilege model with it.
- [ ] A missing prerequisite — no .NET runtime, no Ollama, no model pulled — is a clear message
      naming what to install, not a crash or a silent degrade.
- [ ] No key, no vault path and no absolute path from this machine is baked into the artifact.
      Verified by inspecting the built package, not by trusting the source.
- [ ] Uninstalling removes what was installed and leaves what the user made: the vault is untouched,
      and the workspace, audit log and memory index are either kept or removed on an explicit choice.
- [ ] **It runs on a machine that is not this one.** See the risk below - this criterion is
      `DOĞRULANAMADI` until a second machine exists, and the milestone does not pass on the strength
      of it working here.
- [ ] `/threat-check` reports clean, with attention to what the package grants: an installer writes
      files and creates shortcuts, which is a new surface this project has not had before.

**The risk, stated up front like U1 was.** Every other milestone could be verified on this machine.
This one cannot: "somebody else can install and run it" is exactly the claim a developer's own
machine is worst at testing, because everything it needs is already there. A clean Windows VM is
the honest instrument, and until one is used the packaging criterion above stays unverified rather
than being marked passed on a successful run here.

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
