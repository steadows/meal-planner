# Adversarial review brief: wiring P1 (`meals/background.py`), pre-PR

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement, because the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one. Your job is to find issues that a thorough first-pass review
MISSED, not to repeat what it already found. Findings only. Do not edit, create, move or revert
any file in the repo, and run no git write commands. If an action is denied, stop and report it;
never work around it. Scratch work goes only under `/tmp`.

## Data boundary (hard rule)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.github/`, `CLAUDE.md`,
`.context/seams/P1.md` and `.context/reviews/p1-*`. You must NOT read `.env`, anything under `data/`, `seed/`, or any real
Telegram, Mealie or Meijer content. Describe data by its shape, never its contents.

## Deference rule

The dividing line for trusting a comment, docstring or design-doc section is not code vs docs. It
is authored-by-this-diff vs pre-existing. Anything the diff adds or changes is the author's claim,
and you must verify it against the code before you clear a finding on its authority. That
includes every docstring in `meals/background.py` and the whole of `.context/seams/P1.md`, which
is the author's own design note for this diff. Anything that predates the diff is the standard
the code is measured against: `docs/adr/ADR-0001-runtime-model.md` (accepted by Steve),
`docs/PLAN.md`, `docs/AUTONOMOUS_WORK.md`, `meals/contracts.py`, `meals/claude_runner.py`. Where
they disagree, the pre-existing contract wins unless the diff explicitly and defensibly amends it.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
caller not yet written) is not a false positive just because the triggering condition hasn't
happened yet. The callers of this module don't exist yet: the bot lane's `/find` handler will call
`start`, and the bot plus the P2 jobs (`sun_autoapprove`, `reconcile`) will call `spawn_job`
(ADR-0001 Ownership, :266-295). Judge the module against those documented callers.

## The diff to review

**Don't run `uv`, `pytest` or `python` against the repo either.** They write caches such as
`.pytest_cache` and `__pycache__` that the sandbox may deny. Reason from the code: the suite is
already green (24/24; the five CI commands pass on Python 3.11).

**Don't run `git`.** On this Mac, `/usr/bin/git` writes an xcrun cache under `/tmp` that the
sandbox denies (the first dispatch of this brief stopped on exactly that). The diff is already
written for you: read `.context/reviews/p1-diff.patch`, which is `git diff origin/main...HEAD` limited
to `meals/`, `tests/`, `pyproject.toml` and this brief, and `.context/reviews/p1-commits.txt` for the
commit list. Every file named below is in the working tree at HEAD; read it directly.

It is `git diff origin/main...HEAD` on branch `feat/wiring` (commits 25bec98 RED, f49049e GREEN, 0411db2
simplify, bd79edf repair RED, d0bc110 repairs; the older commits in the range are pre-squash
history already on main). Read these in full:
- `meals/background.py` (the whole product change, about 136 lines)
- `tests/test_background.py`
- `.context/seams/P1.md` (the author's contract)
- `docs/adr/ADR-0001-runtime-model.md`: lines 30-60, 245-300 and 440-460
- `meals/claude_runner.py`: `run`, `_slot` and `_run_once`, for how `work` behaves
- `meals/search.py` `find`: the first `work`

Also in the diff: `python-telegram-bot>=22.8` added to `pyproject.toml` and `uv.lock`. Its source is
in `.venv/lib/python3.13/site-packages/telegram/`; read it rather than trusting memory for
`Application.create_task`, `stop`, `reply_text`, `MessageLimit` and `Defaults` handling.

What the module promises, in one paragraph: `start` replies an ack, runs `work` in the default
executor, and replies `render(result)`. It returns at once. At most 2 requests are in flight (a
module counter touched only on the loop thread), and a third gets BUSY_TEXT without its work
running. After 15 minutes it replies TIMEOUT_TEXT but keeps the slot until the worker thread
really ends. Every reply is plain text (`parse_mode=None`) and split at 4096 characters. Any
failure produces one "Sorry, that didn't work: <first line>" reply and nothing escapes the task.
`spawn_job` starts `python -m meals job ...` in a new session with stdin closed, and lets OSError
reach the caller.

## What the first-pass review already caught

Built-in `/code-review` plus a dedicated observability, dependency and rollback reviewer, each
finding confidence-scored. Kept at >= 75 and FIXED in bd79edf/d0bc110:
- `meals/background.py:95-103` (MEDIUM): a render over 4096 chars was lost to BadRequest; `_reply` now chunks.
- `meals/background.py:57-60` (MEDIUM): a failed BUSY_TEXT send escaped `start`; it is now logged and swallowed.
- `meals/background.py:110-112` (MEDIUM): the abandoned-work end was logged at INFO; it is now WARNING.
- `tests/test_background.py`, the hung-work test (MEDIUM): the abandoned-end log was untested; it is now asserted.
- `tests/test_background.py`, the autouse fixture (MEDIUM): no leak check after each test; it now asserts `_in_flight == 0`.

Scored 50-74, downgraded to LOW and not fixed:
- `meals/background.py:84` (LOW): `except Exception` misses SystemExit from `work`. No reachable source in `meals/`, and catching BaseException would swallow CancelledError.
- `meals/background.py:25` (LOW): the 15-min deadline is shorter than claude_runner's worst case (2 attempts x 600 s plus the slot wait), so a late success is discarded. 15 min is the ADR's decision; the seam-map wording is corrected.
- `meals/background.py:16-18` (LOW): the telegram imports are annotation-only and could sit under TYPE_CHECKING. Declined: the jobs import PTB anyway to send messages.
- `meals/background.py:79,85,112` (LOW): the log lines carry no message or chat id. Declined at one user and a cap of 2.

Scored < 50, discarded:
- `meals/background.py:62` (0): cancelling the task before its first step leaks the slot. PTB never cancels create_task tasks while serving; only shutdown.
- `meals/background.py:84` (0): a TimedOut send may have been delivered, so a false "Sorry" follows. Deliberate and pinned by a test.
- `tests/test_background.py`, FakeApplication (0): `update=` is not pinned. `_run` catches everything, and there's no persistence.
- `tests/test_background.py`, the Popen alias loop (40): excess apparatus. The spec-watchdog judged it worth keeping.
- branch merge commits and being behind main (25): squash-merge makes it immaterial, and it will be merged up before the push.

## Your mandate

1. SKIP anything already covered above. No duplicates. You MAY re-examine a line above at a
   different severity if you find a distinct, worse failure mode there.
2. Focus on the blind spots automated reviewers commonly miss:
   - concurrency and ordering between the loop thread, the executor thread, done-callbacks and
     PTB's own task tracking
   - the slot counter's invariants on every path, including double release
   - what happens to the counter and to pending callbacks across `Application.stop()`
   - trust boundaries in the reply text (Claude output, web text, exception text)
   - cascade failures when Telegram is down
   - `spawn_job`'s child lifecycle under launchd and its fd and session isolation
   - backward-compatibility traps for the bot lane, which will build against this API next
   - and whether the tests could pass while the product is wrong
3. For each finding give: **File**, **Line**, **Category** (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), **Severity** (CRITICAL | HIGH | MEDIUM),
   **Finding**, **Evidence** (why it is real, not speculative; cite code), and **Fix**.
4. If the first pass was thorough and you can't find material issues, say so explicitly. Do not
   invent findings to justify your existence.
