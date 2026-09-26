# Adversarial review brief: wiring P2 (`plan_state`, `jobs`, the job CLI), pre-PR

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement, because the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one. Your job is to find issues that a thorough first-pass review
MISSED, not to repeat what it already found. Findings only. Do not edit, create, move or revert
any file in the repo, and run no git write commands. If an action is denied, stop and report it;
never work around it. Scratch work goes only under `/tmp`.

## Data boundary (hard rule)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.github/`, `CLAUDE.md`,
`.context/seams/P2.md` and `.context/reviews/p2-*`. You must NOT read `.env`, anything under
`data/`, `seed/`, or any real Telegram, Mealie or Meijer content. Describe data by its shape, never
its contents.

## Deference rule

The dividing line for trusting a comment, docstring or design-doc section is not code vs docs. It
is authored-by-this-diff vs pre-existing. Anything the diff adds or changes is the author's claim,
and you must verify it against the code before you clear a finding on its authority. That includes
every docstring in the new modules and the whole of `.context/seams/P2.md` (decisions D1–D11 and
"GREEN notes"), which is the author's own design note for this diff. Anything that predates the
diff is the standard the code is measured against: `docs/adr/ADR-0001-runtime-model.md` (accepted
by Steve), `docs/PLAN.md`, `docs/AUTONOMOUS_WORK.md`, `meals/contracts.py`, `meals/db.py`
(migration 3, `job_run`), `meals/claude_runner.py` (`run(hold_fds)`, the flock rules). Where they
disagree, the pre-existing contract wins unless the diff explicitly and defensibly amends it.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
caller not yet written) is not a false positive just because the triggering condition hasn't
happened yet. Callers not yet written: the bot lane will call `plan_state.get_week`, `transition`
and `latest_run` (approval, "ordered", "retry cart") and spawn `cart_fill`/`sat_propose` jobs; the
cart lane's `cart.fill(cart_list, hold_fds)` will replace `__main__`'s refusing `fill` (P4.1); P3
installs five launchd agents that run these jobs hourly with `RunAtLoad`. Judge the code against
those documented callers.

## What the first-pass review already caught (skip these; re-examine a line only at a different severity)

Scored by confidence (75 = kept at its severity, 50–74 = LOW). The author will fix all of these.

| # | file:line | severity | finding |
|---|---|---|---|
| 1 | meals/jobs.py:262 | HIGH | cart_fill catch-all failure message lacks the "cart may hold items, empty it" warning while `--retry` accepts `failed`, so a retry can refill over a partial cart |
| 2 | meals/jobs.py:203 | MEDIUM | `_busy` uses `running()` (oldest unfinished claim in any week), which can be a past week's claim left unfinished forever → false stale alerts / wrong "since" |
| 3 | meals/jobs.py:258, meals/__main__.py:53 | LOW | exceptions inside `_catch_all` (DB locked, PlanStateError) or in `_busy`/`_job_lock` escape run_job; `__main__` has only try/finally → no Telegram, no jobs.log |
| 4 | meals/plan_state.py:89 | MEDIUM | `JobRun.model_validate(dict(row))` over `SELECT *` with `extra="forbid"`: a future job_run column breaks every read |
| 5 | meals/jobs.py:496 | LOW | cart_fill `--retry` fills an approved week of any age; `latest_run` has no age limit |
| 6 | meals/jobs.py:540 | MEDIUM | reconcile isn't per-week isolated: `weeks_since` validates every row up front, and `_needs_fill`/`get_run`/`set_mealie_ref` are outside `_publish`'s try |
| 8 | meals/jobs.py:477 | LOW | sun_autoapprove Inspect treats a bot approval (after it died pre-CAS) as its own effect and sends "reusing last week's" about Steve's picks |
| 9 | meals/jobs.py:506 | MEDIUM | `_expire` writes detail 'expired' before sending; a failed send → Inspect later sends "stopped partway… empty the cart (Reason: expired)" though no fill ran |
| 10 | meals/jobs.py:146 | MEDIUM | `claimed = True` is set after `_start` returns; a signal between the claim commit and the flag → catch-all treats the claim as not ours → two failure messages |
| 11 | meals/jobs.py:378 | LOW | `run.now - NUDGE_AFTER` is wall-clock arithmetic on a Detroit-aware datetime; off by an hour across a DST night (verified) |
| 13 | meals/jobs.py:255, meals/__main__.py:125 | MEDIUM | the catch-all's `exc_info=exc` and `logger.exception` log the unvetted `__cause__` chain of MealieUnavailable, contrary to its contract |
| 14 | meals/plan_state.py:103, :211 | MEDIUM | `IN (?, ?, ?)` hardcoded for APPROVED_OR_LATER; adding a status breaks both queries |
| 15 | meals/jobs.py:609 | LOW | `if option.hands_on_min` treats 0 as unknown |
| 16 | meals/jobs.py:199, :228 | MEDIUM | stale alert, busy-retry reply and Inspect's interrupted notice leave no jobs.log line |

Filtered out as ADR-prescribed (score < 50; you may re-raise only with new evidence): #7 a pick
that never imports blocks the week's Mealie publish with only a log line, and ages out after 6 days;
#12 imported slugs aren't written back, so fallback weeks re-import web picks (Mealie copies).

## The diff to review

**Don't run `git`, `uv`, `pytest` or `python`.** On this Mac `/usr/bin/git` writes an xcrun cache
under `/tmp` that the sandbox denies, and the Python tools write caches the sandbox may deny.
Reason from the code: the suite is green (928 passed; ruff, strict mypy and import-linter clean on
Python 3.11).

The diff is already written for you: read `.context/reviews/p2-diff.patch`
(`git diff origin/main...HEAD` on branch `feat/wiring-p2`) and `.context/reviews/p2-commits.txt`.
Every file below is in the working tree at HEAD; read these in full:
- `meals/jobs.py` (the five jobs, the job contract, the lock, signals, the Telegram sender)
- `meals/plan_state.py` (every `weekly_plan` / `job_run` read and write)
- `meals/__main__.py` (the CLI and composition root)
- `meals/background.py` (`split_message`, `first_line`, `spawn_job`)
- `docs/adr/ADR-0001-runtime-model.md` in full (Jobs :82–113, Job contract :114–170, Retries
  :171–183, Orphaned Chrome child :184–203, `job_run` :304–321, Integration Test Points :417–444)
- `.context/seams/P2.md` (the author's contract, D1–D11)
- `meals/db.py` (migration 3), `meals/claude_runner.py` (`run`, `_slot`, the hold_fds rules)
- the tests: `tests/test_plan_state.py`, `tests/test_jobs_cart.py`, `tests/test_jobs_weekend.py`,
  `tests/test_jobs_send.py`, `tests/test_main.py`

python-telegram-bot's source is in `.venv/lib/python3.11/site-packages/telegram/`; read it rather
than trusting memory for `Bot`, `initialize`, `RetryAfter` and the error hierarchy.

## Your mandate

1. SKIP anything in the table above. No duplicates.
2. Focus on the blind spots automated reviewers miss, especially here:
   - **State-machine holes** across `weekly_plan.status` × `job_run` claims × the five jobs × the
     bot's future writes: a sequence of ticks, crashes, Telegram outages, retries and bot approvals
     that leaves a week stuck, double-fills a cart, sends a message twice or never, or finishes a
     claim before its message is delivered.
   - **Concurrency:** the flock job lock (released by close, passed to `fill` via `hold_fds`),
     SQLite transactions and compare-and-set under two processes, signals landing mid-transaction or
     mid-send, the alarm's lifetime.
   - **Result preservation:** anything that rewrites a committed `detail`, or finishes a claim
     whose effect isn't recorded.
   - **Time:** America/Detroit windows, DST, `week_start` vs the local date, UTC SQLite text.
   - **Trust boundaries:** TRUSTED reload of stored proposals, secrets reaching logs or Telegram,
     `spawn_job` arguments.
3. For each finding give: **File**, **Line**, **Category** (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), **Severity** (CRITICAL | HIGH | MEDIUM),
   **Finding**, **Evidence** (the concrete sequence), **Fix**.
4. List the files you actually read. If the first pass was thorough and you find nothing material,
   say so plainly. Don't invent findings.
