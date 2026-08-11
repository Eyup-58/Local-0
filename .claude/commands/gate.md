---
description: Verify a milestone's exit criteria
argument-hint: [milestone-id]
allowed-tools: Read, Glob, Grep, Bash
---

Read the exit criteria for milestone $ARGUMENTS in `docs/ROADMAP.md` and verify each one
individually.

For every criterion give **PASS**, **FAIL**, or **DOĞRULANAMADI** (could not verify), followed by
the evidence: real command output, a test result, or a file quotation.

Rules:

- **Never give PASS without evidence.** If you did not run it, you did not verify it.
- Quote real output. Do not paraphrase it and do not reconstruct what you expect it said.
- If a criterion cannot be checked with the tools available, that is DOĞRULANAMADI with the reason —
  not a FAIL, and never a PASS.
- A criterion that is partially met is FAIL. There is no partial credit at a gate.

End with one line: `GATE $ARGUMENTS: PASS` or `GATE $ARGUMENTS: FAIL`.

If FAIL, list what is missing in priority order.

**Fix nothing.** This command reports. Repairing what it finds is a separate, deliberate step.
