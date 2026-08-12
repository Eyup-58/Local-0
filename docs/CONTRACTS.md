# Local Zero — Contracts

**Status:** M0. Covers the message set M1 needs and nothing more.

The JSON Schema files are the **single source of truth**. This document explains them; it does not
duplicate them. Where the two disagree, the schema is right and this file is a bug.

| Boundary | Schema | Transport |
|---|---|---|
| system (C#) ↔ brain (Python) | `contracts/ipc.schema.json` | Named pipe, newline-delimited JSON, UTF-8 without BOM |
| brain (Python) ↔ ui (TypeScript) | `contracts/ws.schema.json` | WebSocket text frames, one JSON object per frame |

---

## 1. Why two schemas and not one

The telemetry payload is currently identical on both sides, and it is still two files.

The two boundaries version independently. The brain is free to reshape, aggregate, or withhold what
it forwards, and the day it does, a shared schema would force a change on a layer that has no
business changing. Duplication here is deliberate and cheap; coupling would not be.

**Rule:** in M1 the brain forwards `telemetry.sample` unchanged apart from re-stamping the envelope.
The first time it diverges, that divergence gets written into §4 of this document. It is never
allowed to become an undocumented difference between two files that look the same.

---

## 2. The envelope

Every message on both boundaries:

| Field | Type | Notes |
|---|---|---|
| `v` | integer | Contract major version. Currently `1`. |
| `id` | UUIDv4 string | Assigned by the sender, unique per message. |
| `ts` | RFC 3339 UTC, ms precision, `Z` suffix | When the message was created — distinct from when a sample was taken. |
| `type` | string | Discriminator. Determines the shape of `payload`. |
| `payload` | object | Type-specific. |

`additionalProperties: false` applies at **every** level of both schemas. An unknown field is a
rejected message, not an ignored one — that is what stops a field being smuggled past one layer in
the hope that a later one reads it.

### Validation is mandatory, in both directions

- Validate **before reading any field**. Not after, not partially. A message that fails validation
  has no readable fields, including for logging.
- A failed message is **dropped and counted**, never partially applied.
- One bad message does not tear down the connection. Repeated violations do.
- Unknown `v` → drop, reply `unsupported_version`, do not attempt best-effort parsing.

---

## 3. IPC messages (system ↔ brain)

### `hello` — system → brain

First message on every new pipe connection. **The brain must not accept telemetry before a valid
`hello`**; an unsolicited `telemetry.sample` gets `handshake_required`.

Payload: `component` (`"system"`), `app_version`, `elevated`, `poll_interval_ms`, `sensors[]`.

Two fields carry more weight than their size suggests:

**`elevated` is `const: false`.** Local Zero runs every process `asInvoker`. A sidecar claiming
elevation is a contract violation, and the brain refuses the connection rather than trusting a
component that is already behaving unexpectedly. See `ARCHITECTURE.md` §2.

**`sensors[]` is the honesty mechanism.** It declares, once, every field the UI can display and
whether it will ever carry a value on this machine — with `source` and, when unavailable, a
human-readable `unavailable_reason` that the UI shows verbatim.

Without this, the UI sees `cpu.temperature_c: null` and cannot tell a missing sensor from a
transient read failure from a bug. With it, the UI renders *"requires kernel driver — not
installed"* and the user knows exactly where they stand.

The schema enforces the pairing: `available: false` requires a non-null `unavailable_reason` and
`source: "none"`. A silent gap is a rejected message.

### `telemetry.sample` — system → brain

One reading. Emitted on the sidecar's own tick; **the brain never polls**.

- `seq` increases monotonically from 0 per connection. A gap means samples were dropped and the
  consumer can say so.
- `sampled_at` is when the machine was read; the envelope `ts` is when the message was built. Under
  load these differ, and conflating them makes latency unmeasurable.

**Null means unavailable.** Every numeric field is nullable, and null is rendered as a labelled gap.
It is never rendered as zero, never interpolated, never inferred from load. On this machine
`cpu.temperature_c` and `gpu.temperature_c` are expected to be null permanently — see
`ARCHITECTURE.md` §3.

**`per_core_percent` carries nulls of its own, and position is identity.** Index 5 is core 5,
always. A null entry means that core reported nothing for this sample, because Windows parks idle
cores and a parked core has no utilization to report. Measured on the target machine 2026-08-11:
PDH returns all 30 instances every time, but 3–6 of the 28 cores come back with a non-success
`CStatus` on roughly half of all samples, and they are almost always the E-cores of the i7-14700KF.

The array is therefore **either null entirely or complete**, with nulls holding the places of cores
that did not report. It is never compacted. Dropping an entry shifts every core after it, and the
UI then draws one core's load on another core's bar — a number in the wrong place, which is worse
than no number, because the labelled gap is visible and the misaligned bar is not.

### `error` — either direction

Reports a fault without terminating the connection. `code` is a closed enum; `message` is
user-safe prose under 500 characters; `in_reply_to` carries the offending message's `id` or null.

Neither field may contain a secret, a stack trace, or a filesystem path outside the allowed roots.
Detail for debugging goes to the log, not onto the wire.

---

## 4. WebSocket messages (brain ↔ ui)

### `client.hello` — ui → brain

First frame the UI sends. The brain streams nothing before it.

### `server.hello` — brain → ui

Sent once in reply. Carries `poll_interval_ms`, the forwarded `sensors[]`, and `system_connected`.

An empty `sensors[]` is legal and means the system layer has not handshaked yet — the UI shows a
starting-up state, not an empty panel that looks like zero sensors exist.

### `system.status` — brain → ui

Emitted whenever the system layer connects or drops.

This message is why the UI can degrade honestly. Without it, a dead sidecar looks identical to an
idle machine: the numbers simply stop changing. With it, the UI marks the data stale and stops
presenting the last sample as live. It is an M1 exit criterion, not a nicety.

### `telemetry.sample` — brain → ui

Forwarded. See §1.

### `error` — brain → ui

Same shape as the IPC error, different code enum (`system_unavailable` replaces
`sensor_read_failed`).

**Every string in every message is rendered as text.** The UI imports no markdown renderer on this
path and `dangerouslySetInnerHTML` is banned repository-wide. See `SECURITY.md` §5 — this is a
security control, not a styling choice.

### `approval.request` — brain → ui

Raised when an invocation has passed the name whitelist, the argument schema and path containment,
and needs a human. Payload: `request_id`, `capability`, `resolved_args`, `affected_paths[]`,
`side_effect`, `origin`.

**The brain builds this, not a model.** `resolved_args` is the arguments *after* validation and path
canonicalisation — what will actually run, not what was asked for. `affected_paths` is computed, not
narrated. `SECURITY.md` §5.

`resolved_args` values are **scalars only**. A nested structure is a rendering decision waiting to be
got wrong, and rendering decisions are exactly where markup re-enters a payload the user is reading
in order to decide. `rejected/ws.approval-nested-args.json` holds that line.

### `approval.decision` — ui → brain

Payload: `request_id`, `decision` (`approve` | `reject`).

This is the entire extent of the UI's authority over an invocation: answer one the brain already
resolved. It cannot construct, alter or re-scope one. `decision` is a closed enum, so a value the
brain cannot interpret fails closed rather than being read as either answer.

### `approval.resolved` — brain → ui

Payload: `request_id`, `outcome` (`approved` | `rejected` | `expired` | `auto_approved`).

Closes a request out so no dialog lingers over something already settled. `auto_approved` is what
trust mode produces — it exists so operations the button let through are still *visible*, rather than
absent because no dialog was ever raised.

### `trust.status` — brain → ui, and `trust.set` — ui → brain

`trust.status` carries `enabled` and `since`; `trust.set` carries `enabled`.

Trust mode bypasses the approval gate for every invocation regardless of `side_effect` or `origin`.
It does **not** bypass the guard: the name whitelist, the argument schema and path containment run in
every mode. See `SECURITY.md` §5.

`trust.set` is the only way the state changes, and nothing but the UI can send it. It is not a
registered capability, so no invocation can reach it, and the state file it writes lives outside
every capability's `allowed_roots`.

**Why trust state is its own message rather than a field on `server.hello`:** `additionalProperties`
is `false` everywhere, so adding a field to an existing payload is a breaking change under §5. A new
message type is additive. The contract shape follows from the versioning rule rather than from
convenience.

### `provider.status` — brain → ui

Payload: `mode` (`local` | `cloud`), `model`, `has_key`, `since`.

Which model layer is in use and whether a cloud key is stored. Sent after `server.hello` and whenever
either changes, for the same reason `trust.status` is: a tab that does not yet know the boundary is
open would show the safe state while the permissive one is in force, which is the wrong way round to
be wrong.

`has_key` is a boolean and that is the whole of it. **The key is never sent to the UI** — not the
value, not a prefix, not its length. `rejected/ws.provider-status-carrying-the-key.json` is the file
that keeps a later well-meaning "just show the last four characters" change from being made quietly.

### `provider.select` — ui → brain

Payload: `mode`.

Switches the network boundary described in `SECURITY.md` §11. Selecting `cloud` with no key stored is
**refused with an error** rather than accepted — leaving the egress guard open while nothing can
authenticate would be the worst of both states.

Like `trust.set`, this is the user's own switch rather than an invocation: it is not a registered
capability, so nothing a model proposes can reach it.

### `credential.set` — ui → brain

Payload: `key` (non-empty, ≤ 4096 characters).

The cloud key, crossing the loopback socket once on its way into the Windows Credential Manager. The
frame is **never logged, never audited, and never echoed in a validation error**; the brain
acknowledges it with a `provider.status` carrying `has_key`, not with the value. The UI clears its
field on send and never renders the value back.

An empty key is refused at the schema (`minLength: 1`). Accepting one would store it, report
`has_key: true`, and then fail as an authentication error somewhere with nothing pointing back here.

### `memory.status` — brain → ui, and `memory.reindex` — ui → brain

`memory.status` carries `enabled`, `vault`, `notes`, `chunks`, `embedded_chunks`, `last_indexed_at`
and `embeddings_available`. `memory.reindex` carries nothing.

Memory being **off** is an ordinary state — no vault configured, or a renamed folder — and
`enabled: false` says so with the counts at zero, rather than the UI showing an empty vault as
though it had indexed one.

`embeddings_available: false` means search is ranking by keyword alone because no embedding model
answered. It is reported rather than inferred: search that quietly gets worse is the failure nobody
notices.

**`memory.reindex` carries no path, and that is the point.** A vault to scan, arriving from the UI,
would be a directory to walk, read and index chosen by the least authoritative component on the
socket. The vault is the configured one.
`rejected/ws.memory-reindex-with-a-path.json` holds that line.

**OPEN QUESTION — removing a stored key is not in this contract.** A `key: null` meaning "forget it"
was considered and declined: null is what an accidental empty submit produces, and silently clearing
a stored credential is not something a stray frame should be able to do. Until there is a message
that says so explicitly, removal is done in Windows' own Credential Manager.

### `turn.state` — brain → ui

Carries `state`, `since`, `caption` and `detail`. `state` is one of `idle`, `listening`, `thinking`,
`tool_running`, `speaking`.

**Reported, never inferred.** The UI has no timer that advances this and no elapsed-time heuristic
that guesses the next one. If the brain stops sending `turn.state` the core holds the last state it
was told about, which is the honest thing for it to do — a panel that decided for itself that the
brain was "probably speaking by now" would be narrating.

`caption` is what the brain is saying, in its own words, or `null` when it has nothing to say. Null
is a gap and the UI renders it as one: no greeting, no status line, no filler substituted for
silence. Empty prose is refused rather than accepted as a second spelling of the same thing —
`rejected/ws.turn-state-empty-caption.json` holds that line, because a blank caption renders as a
blank line the user cannot tell apart from a caption that failed to arrive. `detail` is a short
label for what the turn is about, under the same rule.

`ws.turn-state-silent.json` is the valid example of the quiet case: `listening`, both prose fields
null.

### `tool.log` — brain → ui

Carries `at`, `capability`, `message` and `status`. `status` is one of `running`, `ok`, `failed`;
`running` is **not terminal** and nothing may conclude a turn on the strength of it.

One message per event, appended as it happens, rather than a replayed array. A UI that reconnects
has missed what it missed, and re-sending history would let it draw a log it never actually
observed. What fell off the end belongs to the audit log, which is the record that is supposed to be
complete.

An unknown `status` is refused rather than bucketed into the nearest-looking one:
`rejected/ws.tool-log-unknown-status.json` holds that line, because the nearest-looking one is `ok`
and that would paint a failed call green.

**`message` may paraphrase content the brain fetched, which makes it untrusted text** under
`SECURITY.md` §2. It is safe to display because it is rendered as text and never routed back into
the planner — see `SECURITY.md` §9.

Neither message is in `ClientMessage`: a tab that could send `turn.state` could paint any state it
liked onto the core, and one that could send `tool.log` could write lines into the record of what
ran. `brain/tests/test_turn_state.py` holds that line.

---

## 5. Versioning

`v` is a **major** version. It increments only on a breaking change.

Additive and non-breaking:
- adding an optional field with a safe default
- adding a new value to `sensors[].source`
- adding a new message `type` that existing receivers may ignore

Breaking — requires `v` to increment:
- removing or renaming any field
- changing a field's type or making an optional field required
- changing the meaning of an existing value
- tightening a range or enum such that previously valid messages fail

Because `additionalProperties: false` is set everywhere, **adding a field is breaking for any
receiver already validating strictly.** In practice: additive changes still require both sides to
be updated together. This is a deliberate trade — strict validation catches smuggled fields, and
the cost is that the two ends ship in lockstep. With three layers in one repository, that cost is
low.

### Amendments to v1

One change has been made to v1 in place rather than by incrementing the version.

**2026-08-11 — `per_core_percent` items widened from `number` to `number | null`.** Under the rule
above this is breaking: it changes a field's type, and a receiver validating v1 strictly would
reject the new shape. It was amended in place anyway, by explicit human decision, because M1 is the
first implementation of this contract and **no v1 consumer exists outside this repository** — there
is nothing deployed for the increment to protect. All three layers were updated in the same
change.

This is recorded rather than done quietly, and it is not a precedent: once anything ships, a
breaking change increments `v`. The reason for the change is in §3.

### Additions in M3 — `v` unchanged

**2026-08-12 — five message types added to the WebSocket contract** for the approval flow and trust
mode: `approval.request`, `approval.decision`, `approval.resolved`, `trust.status`, `trust.set`.
`ipc.schema.json` is untouched; the sidecar has no part in approval.

This is **additive**, not an amendment, so `v` stays 1 — the list above names "adding a new message
`type` that existing receivers may ignore" as non-breaking. Nothing was added to an existing payload,
and that constraint shaped the design rather than being discovered afterwards: trust state is carried
by its own `trust.status` message specifically because putting an `enabled` field on `server.hello`
would have been breaking under the `additionalProperties: false` rule above.

Both ends still ship together, as §5 says additive changes require in practice.

### Additions in M4 — `v` unchanged

**2026-08-12 — three message types added** for provider selection and key entry:
`provider.status`, `provider.select`, `credential.set`. `ipc.schema.json` is untouched; the sidecar
has no part in the model layer.

Additive again, so `v` stays 1, and the same constraint shaped the design a second time: the current
mode is carried by its own `provider.status` message rather than by a field on `server.hello`,
because `additionalProperties: false` makes adding that field breaking.

**2026-08-12, M4.5 — two more:** `memory.status` (brain → ui) and `memory.reindex` (ui → brain), for
the memory panel. Additive again, `v` stays 1, and `ipc.schema.json` is untouched: the sidecar has no
part in memory either.

`credential.set` is the first message in this contract whose payload must never be logged. That is a
handling rule rather than a schema rule — a schema cannot express "do not write this down" — so it is
stated here, in `SECURITY.md` §11, and in the schema's own description, and enforced in the brain by
the frame never reaching the audit log or an error message.

**2026-08-12 — two more, for the orchestration-centre panel:** `turn.state` and `tool.log`, both
brain → ui. `ipc.schema.json` is untouched a fourth time: the sidecar has no part in a conversational
turn either.

Additive, so `v` stays 1, and the same constraint shaped the design a third time — the current turn
is its own message rather than a field on `server.hello`, because `additionalProperties: false` makes
adding that field breaking.

Both are **outbound only**. They are deliberately absent from `ClientMessage`, which is the narrowed
inbound union: the UI holds no authority, and a tab able to originate either one could assert a turn
that never happened or a tool call that never ran. That is the same reasoning that keeps
`approval.request` out of the inbound union.

`tool.log.message` is the first field in this contract that may legitimately carry text derived from
content the brain fetched. Like `credential.set`, the rule that matters is a handling rule the schema
cannot express — display as text, never route to the planner — so it is stated here, in
`SECURITY.md` §9, and in the schema's own description.

---

## 6. Change procedure

Contract drift across a three-layer system is the most likely way this project breaks quietly, so
the procedure is mechanical:

1. **A contract change is its own commit.** It is not bundled with an implementation change.
2. **Ask a human first.** Agents do not change `contracts/` on their own initiative. If the code
   needs something the contract does not permit, that is a question, not a licence to bend the code.
3. `docs/CONTRACTS.md` is updated **in the same change** as the schema. A PreToolUse hook
   (`.claude/hooks/guard_contracts.py`) warns when a `contracts/` file is edited, precisely because
   this is the step that gets skipped.
4. Add or update an example under `contracts/examples/`. A breaking change means a new example for
   the new shape and, where useful, a `rejected/` example proving the old shape now fails.
5. Run the validator and quote the real output:

   ```
   uv run --with jsonschema python contracts/validate_examples.py
   ```

6. Update the mirrored types in all three layers in the following commit.

---

## 7. Examples

`contracts/examples/` holds one valid payload per message type. `contracts/examples/rejected/`
holds payloads that **must** fail, each carrying a `_why_rejected` note that the validator strips
before checking, so the message is rejected for its real reason.

The rejected set is the more useful half. It is what turns "schema-invalid messages are dropped"
from an assertion into a test:

| Example | Proves |
|---|---|
| `ipc.elevated-true.json` | A sidecar claiming elevation is refused |
| `ipc.unavailable-without-reason.json` | An unreadable sensor cannot be silently blank |
| `ipc.unknown-field.json` | An extra field (`exec`) cannot ride along inside a valid message |
| `ipc.per-core-out-of-range.json` | A core reading above 100% is refused even though nulls are now legal |
| `ws.unsupported-version.json` | Version mismatch fails closed |
| `ws.approval-unknown-decision.json` | A decision the brain cannot interpret fails closed rather than being read as either answer |
| `ws.approval-nested-args.json` | `resolved_args` values stay scalar, so markup cannot re-enter the payload the user reads to decide |
| `ws.credential-set-empty.json` | An empty key is refused where the mistake is, not later as an authentication failure |
| `ws.provider-status-carrying-the-key.json` | The key cannot ride back to the UI inside a status message |
| `ws.memory-reindex-with-a-path.json` | The UI cannot choose a directory for the brain to walk and index |
| `ws.turn-state-empty-caption.json` | Silence is spelled `null`; an empty caption cannot become a blank line indistinguishable from one that failed to arrive |
| `ws.tool-log-unknown-status.json` | An unreadable status fails rather than rounding to the nearest, which would paint a failed call green |

One valid example is worth naming for the same reason: `ws.approval-request-untrusted.json` is a
**valid** message whose `content` argument carries `<img src=x onerror=alert(1)>` and an instruction
to approve itself. It is legal on the wire on purpose — the contract's job is not to filter that, and
attempting to would produce the illusion of cleaning. Rendering it as literal text is the UI's job,
and this file is the fixture that proves it does.

The validator selects the schema by filename prefix (`ipc.` / `ws.`) and narrows to the single
message definition matching the `type` field before validating — otherwise the top-level `oneOf`
collapses every failure into "not valid under any of the given schemas", which cannot distinguish a
message rejected for the intended reason from one rejected by accident.

Current state, verified 2026-08-12: **35/35 expectations hold** (23 valid, 12 rejected).

---

## 8. The Python end of the named pipe — decided in M1

**Decision: `pywin32` (`win32file` / `win32pipe` / `win32event`), with a dedicated reader thread.**
Taken 2026-08-11 against working code, as M0 said it would be. Implemented in
`brain/local_zero_brain/ipc/pipe_client.py`.

Python's standard library has no Windows named pipe support, so the choice was between `pywin32`
and abandoning the pipe for a `127.0.0.1` socket. The socket is simpler code and natively
asyncio-aware, and it forfeits the only reason the transport was chosen: a pipe carries an
OS-enforced ACL, while a loopback listener is reachable by every process running as any user on the
machine and can only be defended with an application-level secret that has to be stored somewhere.
That somewhere becomes the new weakest link, on a channel whose messages will eventually authorize
OS actions.

`pywin32` handles are not asyncio-aware, so the brain runs one reader thread feeding an
`asyncio.Queue` through `loop.call_soon_threadsafe`. That cost was known in M0 and is what it
looked like in practice.

**Measured while implementing it — the pipe must be opened for overlapped I/O.** The first version
used a synchronous `ReadFile`. A synchronous read on a pipe with no data blocks until data arrives,
and on Windows closing the handle from another thread does **not** reliably unblock it: the reader
thread could not be stopped and the test suite hung on shutdown. The fix is `FILE_FLAG_OVERLAPPED`
plus a `WaitForMultipleObjects` on the read's completion event and a stop event.

Polling with `PeekNamedPipe` would also have worked, at the cost of a syscall every few
milliseconds forever. A layer that reports on machine resource use has no business burning idle CPU
to do it — see `PERFORMANCE.md` budget P5 — so the read waits on an event and costs nothing while
idle.

Evidence: `brain/tests/test_pipe_client.py` exercises a real Windows named pipe rather than a mock,
including a regression test asserting that a client with nothing to read still stops promptly.

If this is ever revisited in favour of loopback TCP, that is a `SECURITY.md` amendment before it is
a code change.
