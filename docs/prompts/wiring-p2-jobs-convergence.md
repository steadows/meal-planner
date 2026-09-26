# Convergence brief: wiring P2, PR #26, after ultrareview round 1

You are a single adversarial reviewer working ALONE. Do NOT spawn sub-agents or run a fleet: this is
a narrow convergence check, not a new review. Findings only. Do not edit, create, move or revert any
file in the repo, and run no git write commands. If an action is denied, stop and report it; never
work around it.

**Don't run `git`, `uv`, `pytest` or `python` against the repo** (the sandbox denies their cache
writes on this Mac). The suite is green: 961 passed on Python 3.11; ruff, strict mypy and
import-linter clean. Reason from the code.

**Data boundary:** read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `CLAUDE.md`,
`.context/seams/P2.md` and `.context/reviews/p2-*`. Never read `.env`, `data/` or `seed/`.

## What to check

The ultrareview fleet's round 1 (`.context/reviews/p2-ultra-1.md`, verdict NOT READY) reported five
findings. The author repaired all five RED-first: tests in commit 3842ec0, fixes in 7167b2b. The
repair diff (both commits) is pre-written at `.context/reviews/p2-ultra-1-repairs.patch`. The design
decisions are D23–D27 in `.context/seams/P2.md`. The whole PR diff is `.context/reviews/p2-diff.patch`.

For each finding U1–U5, answer: **CLOSED** or **OPEN**, with one or two sentences of evidence from
the code at HEAD (`meals/jobs.py`, `meals/background.py`) and the new tests.
- U1: a signal during Inspect's report redelivery must not produce "empty the cart" for a filled cart.
- U2: an undelivered Sunday auto-approve message must be redelivered after Sunday (reconcile spawns
  `sun_autoapprove --week W`).
- U3: TelegramSend's 2-minute budget must bound each attempt, a successful slow one included.
- U4: a retry must log the prior claim before resetting it.
- U5: a cart report under $35 must warn about the pickup fee, on redelivery too.

Then look for **regressions the repairs themselves introduce**, and only those. Examples:
- the D23 catch-all change altering a path the earlier decisions pinned (D12 advice on a real
  mid-fill failure, D18 "ours" after a signal right after the claim);
- D24's respawn looping or double-approving;
- D25's `wait_for` cancelling mid-send in a way that loses a result or leaks the token;
- D26 changing what a refused retry writes.

Report only a regression you can show with a concrete sequence. Don't re-review the rest of the PR,
and don't re-raise dispositions recorded in `.context/reviews/p2-code-review.md` or the PR's
"Decisions for Steve".

## Output

A table of U1–U5 with CLOSED or OPEN plus evidence. Then any regressions, each with file, line,
severity (CRITICAL/HIGH/MEDIUM/LOW), the sequence and a fix. End with an overall verdict: **CLOSED**
(nothing open, no regressions) or **NOT CLOSED**. List the files you actually read.
