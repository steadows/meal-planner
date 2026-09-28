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

## Pass 2 (after convergence 1)

Convergence 1 (`.context/reviews/p3-convergence-1.md`) closed U1. It found one regression: the README promised
that `reconcile` resends a finished fill's cart report within the hour, which is false for a week that started
more than six days ago (`RECONCILE_DAYS`).

The repair is commit after 1254cad, docs-only. Both README commits are pre-written at
`.context/reviews/p3-convergence-1-repairs.patch`. The README now:
- makes redelivery conditional;
- if no report comes, says to check `weekly_plan.status` with `sqlite3`: `cart_filled`/`ordered` keeps the cart,
  and `approved` means empty it and reply "retry cart".

For pass 2, answer only:
- Is the regression CLOSED?
- Is the status check correct per `meals/plan_state.py` and `meals/jobs.py`:
  - which status a committed fill records (`record_cart`);
  - what `approved` implies about the cart;
  - whether "retry cart" then works for that week (`_retry_refusal`, the six-day limit);
  - whether `data/pantry.sqlite` is the database `get_db` opens by default?
- Did it introduce a new regression? Show it with a concrete sequence.

Same rules as above: single agent, no git/uv/pytest/python/scripts, findings only. End with **CLOSED** or
**NOT CLOSED**.

## Pass 3 (after convergence 2)

Convergence 2 (`.context/reviews/p3-convergence-2.md`) closed the pass-1 regression. It found that the README sent
every `approved` week to "retry cart", although a week more than six days old, or one whose claim is still
unfinished, can't be retried at once.

The repair is docs-only, the last commit on the branch. It's pre-written, with the pass-1 repair, at
`.context/reviews/p3-convergence-2-repairs.patch`. The README no longer promises the retry works. It says the reply
explains why a week can't be retried:
- more than six days old: no refill;
- never settled: a second "retry cart" after its interrupted notice.

For pass 3, answer only:
- Is that regression CLOSED?
- Is the new sentence true per `meals/jobs.py`: Inspect's handling of an unfinished claim on `--retry`,
  `_retry_refusal`, and the six-day limit?

Report a new regression only with a concrete sequence in which following the README loses a filled cart, doubles
a cart, or states something the code contradicts. Same rules as above. End with **CLOSED** or **NOT CLOSED**.
