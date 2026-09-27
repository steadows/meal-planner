# Convergence brief: wiring P3, PR #29, after ultrareview round 1

You are a single adversarial reviewer working ALONE. Do NOT spawn sub-agents or run a fleet: this is a narrow
convergence check, not a new review. Findings only.

**Rules:**
- Don't edit, create, move or revert any file in the repo, and run no git write commands.
- **Don't run `git`, `uv`, `pytest`, `python`, any script in `deploy/`, or `launchctl`.** Reason from the code.
- If an action is denied, stop and report it; never work around it.
- **Data boundary:** read only `deploy/`, `meals/`, `tests/`, `docs/`, `.context/seams/P3.md` and
  `.context/reviews/p3-*`. Never read `.env`, `data/` or `seed/`.

## What to check
The ultrareview fleet's round 1 (`.context/reviews/p3-ultra-1.md`, verdict NOT READY) reported one finding, U1:
- **The finding:** the README's Stuck-run steps told the operator to empty the cart when a message says the fill
  "failed".
- **Why that's wrong:** the outer handler's generic `job cart_fill failed: …` (`meals/__main__.py` `_report`)
  doesn't establish whether the fill committed. Emptying could discard a completed cart.

The repair is docs-only, commit 1254cad. It's pre-written at `.context/reviews/p3-ultra-1-repairs.patch`; the file
at HEAD is `deploy/README.md`.

1. **Is U1 now CLOSED or OPEN?**
   - Check the new wording against every cart_fill message the code can send:
     - `meals/jobs.py`: `_EMPTY_THE_CART`, the notices, the catch-all and its `ours` advice rule, and the Inspect
       redelivery of the cart report;
     - `meals/__main__.py`: `_report`, `_report_startup_failure`.
   - Would following the steps ever empty a cart whose fill committed?
   - Would they ever leave a partial cart un-emptied when the code actually asked for emptying?
   - Is the claim "if the fill had finished, reconcile resends its cart report within the hour" true per
     `_reconcile` and `_needs_fill`?
2. **Did the repair introduce a regression?** Report only one you can show with a concrete sequence.

**Don't re-review the rest of the PR**, and don't re-raise dispositions recorded in
`.context/reviews/p3-code-review.md` or the PR's "Decisions for Steve".

## Output
- U1: CLOSED or OPEN, with evidence (file:line).
- Any regression: file, line, severity, sequence, fix.
- End with **CLOSED** or **NOT CLOSED**, and list the files you read.
