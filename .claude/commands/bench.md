---
description: Measure against the performance budgets
allowed-tools: Read, Bash
---

Run the measurement scripts in `bench/` and report against the budgets in `docs/PERFORMANCE.md`.

Rules:

- **Report only measured numbers.** No estimates, no extrapolation, no "roughly".
- If a metric cannot be measured, write **"not measured"** and state why. Never substitute a guess.
- Quote the script's real output alongside your summary.
- Name the script and the date for every number.
- Report p95 and max against the budget, not the average — an average hides the stall that a human
  actually notices.
- Check each script's stated traps before trusting its number: PDH needs two samples before a rate
  is meaningful, working-set readings taken under system memory pressure are discarded, and a
  cross-process latency figure is worthless until the clock offset has been established.

For each budget give **WITHIN / OVER / NOT MEASURED**.

If a budget is missed, say so plainly. Do not adjust the number in `docs/PERFORMANCE.md` to make it
pass — a missed budget is either a code problem or a budget that needs renegotiating against the
measured floor, and both are decisions for a human.
