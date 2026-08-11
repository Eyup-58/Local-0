---
description: Trust boundary and privilege surface audit
allowed-tools: Read, Glob, Grep, Bash
---

Audit the changed code against the threat model in `docs/SECURITY.md`.

Work through these in order. Give a `file:line` reference for every finding, and for every item
where you found nothing, say what you searched so the absence is meaningful.

1. Does untrusted content (LLM output, file contents, web content, memory records, telemetry) flow
   into a capability argument, a path, or a command as an instruction?
2. Are the arguments of a whitelisted invocation validated separately from the name?
3. Is every path canonicalised before the `allowed_roots` check? Is escape possible via `..`, an
   absolute path, a symlink, or a junction? Is the check done on the resolved path rather than the
   input string?
4. Is any command line built by shell interpolation or string concatenation?
5. Is the named pipe's ACL set explicitly rather than left to defaults? Does it grant only the
   current user's SID? Does any process request elevation?
6. Can a `destructive` path reach execution without approval? Can any field of the approval payload
   be influenced by the LLM?
7. Can a rejected operation be retried in the same session?
8. Is a secret exposed in a log, an error message, a test fixture, or a commit?
9. Is `System.Diagnostics.PerformanceCounter` or any localized counter path present? (Invariant L1 —
   it half-works on this machine and reads as a sampler bug.)
10. Is any unavailable sensor rendered as zero rather than as a labelled gap?

Classify every finding **CRITICAL / HIGH / MEDIUM / LOW**. If there are none, write "clean" and say
what you covered.

**Report only. Fix nothing.**
