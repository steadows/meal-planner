# Adversarial review brief — bot PR 2b: update_id claim and free text → pantry (`meals/bot/`)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Find the issues a thorough first-pass review MISSED; do not
repeat what it already found. Findings only: do not edit, create or delete any file.

## Boundaries

- Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, the `.context/reviews/pr2b-*`
  files named below, plus installed python-telegram-bot under
  `.venv/lib/python3.11/site-packages/telegram/` for behaviour checks. Never read `.env` (a symlink
  to real secrets), anything under `data/`, `seed/` or `deploy/`, or any real Telegram, Mealie or
  Meijer content.
- You do not need git: the working-tree diff against main 28426e2 is pre-written at
  `.context/reviews/pr2b-green.diff`. The tests are committed separately (RED, frozen) under
  `tests/test_bot/`; read them as the executable spec. You may run
  `.venv/bin/python -m pytest tests/test_bot -q -p no:cacheprovider` or a small
  `.venv/bin/python -c ...` probe. If a command is denied, carry on with a static review. Cite
  file:line for every claim.

## Deference rule

What matters for trusting a comment, docstring or design doc is whether this diff authored it or it
was already there. Anything the diff adds is the author's claim: verify it against the code before
clearing a finding on its authority. Anything that predates the diff is the standard.
- Pre-existing (the standard): `meals/contracts.py` (Intent union, `PantryFlipIntent`, `Pantry`
  Protocol), `meals/pantry.py`, `meals/db.py` (migration 4 `telegram_update`), `meals/background.py`
  (`start`, `_run`, `DEADLINE_S`, `ERROR_MAX_CHARS`), `meals/claude_runner.py` (`run(..., web=)`,
  `load_prompt`, `ClaudeRunnerError`), `meals/rollup.py` (`name_key`), `docs/PLAN.md`,
  `docs/adr/ADR-0001-runtime-model.md`, `CLAUDE.md`, and PR 2a's merged `meals/bot/` code (main).
- Authored by this PR: the diff (`meals/bot/__init__.py`, `gate.py`, `handlers.py`, `intents.py`,
  `pantry_ops.py`, `meals/prompts/intents/parse.md`, the PLAN.md :482 rewording), the RED tests,
  and the contract file below.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the triggering condition hasn't occurred
yet. If the code as written produces the wrong behaviour once that condition is real, it is a live
finding now.

## The done contract — what this change promised

Read `.context/reviews/pr2b-contract.md` in full: Done means, the AC table, Out of scope (a fence),
Budget, decisions D1-D8, the F2 note, "Carried into 2b", PR split rev 4, and the last sections
("PR 2b seams as built", the RED rulings, the watchdog ruling, "After 2b GREEN and /simplify"),
which win where they differ. This PR proves AC-2 (claim failure), AC-3, AC-4, AC-6, AC-7, AC-8,
AC-9, AC-11, AC-12 and the run_bot gate connection. The done check's Acceptance → proof table is at
`.context/reviews/pr2b-done-check.md`: every one of those rows passes (109/109 bot tests, 1300 full
suite, ruff/mypy/lint-imports clean).

## Bin every finding

| Bin | When | What happens |
|---|---|---|
| **FIX NOW** | It breaks an AC or the team DoD, or it is a correctness/security/data-loss defect in code this story adds or changes | Fixed in this PR, or in a follow-on PR under the same story |
| **DROP** | A nit, style, or speculative future concern; OR pre-existing and outside this story's surface; OR it sits in the story's Out of scope list | One line in the PR summary. No ticket |
| **LATER** | A real defect that can't be fixed here, for exactly one reason: `post-PoC` · `other-owner` · `needs-ruling` (a Steve product call) · `needs-design` | One line on the story's Later list, with its reason. No ticket |

Severity doesn't pick the bin; scope does. A finding that lands in the Out of scope list is DROP:
one line, no argument.

## What the first-pass review already caught (after confidence scoring)

Surviving (all scored 50, so LOW):
1. `meals/bot/handlers.py:190` / `pantry_ops.apply` (LOW): `_text_work` catches only
   ClaudeRunnerError, so a pantry error (sqlite3.OperationalError) on a later intent loses the echo
   lines of intents already written; background replies one generic error line.
2. `meals/bot/handlers.py:186` (LOW): `claude_runner._slot()` waits without a deadline, so the
   free-text work is unbounded while other processes hold every Claude slot (other-owner:
   contracts; claude_runner is not in this diff).
3. `meals/bot/pantry_ops.py:33` (LOW): the "don't know X" and "ran out of time to update X" replies
   echo Claude's raw item string, and `Text` allows an inner newline, so one intent's echo can span
   lines (AC-6).
4. `meals/bot/handlers.py:243` (LOW): run_bot opens PANTRY_DB twice (#38's probe, then gate_conn);
   a failure on the second open escapes as a raw sqlite3 error instead of the startup refusal.
5. `meals/bot/pantry_ops.py:36-47` (LOW): refused writes (deadline expiry, newer message wins,
   unknown item, unsupported kind) log nothing; a late refusal is discarded by background unseen.
6. `meals/bot/handlers.py:172/198/153` (LOW): no update_id or ticket ties a parse or write back to
   an update.

Discarded (score < 50; you may re-raise one only with new, concrete evidence):
- `gate.py:53`: a claim error stops the update with no chat reply (0; designed per AC-2 and rev 4).
- `gate.py:24`: claim's synchronous BEGIN IMMEDIATE on the event loop can stall dispatch up to the
  5 s busy timeout (0; the contract puts the claim on the event loop).
- `intents.py:29`: json.dumps leaves `<`/`>` unescaped, so a message could close the
  `<untrusted_data_only>` block (0; only Steve's chat reaches the parse, the run has no tools, and
  the worst write is an existing item flipped to buy_next_time).
- `handlers.py:89`: unknown commands get no reply (0; pre-existing; /due and /plan are 2c).
- `handlers.py:185`: the pantry connection stays open across the parse (25; no lock or open
  transaction is held).
- `meals/prompts/intents/parse.md`: prompt folder named after the module, not the lane (25; PLAN
  assigns `intents` to the bot lane; search/planner do the same).

## Your mandate

1. SKIP anything already covered above. No duplicates.
2. Focus on blind spots:
   - Subtle logic errors under specific input combinations (several intents about the same item in
     one message; alias vs canonical name; an unknown item that a later intent in the same message
     names correctly; a ticket equal to the recorded one).
   - Cross-file interaction (gate claim vs the error handler from #38; background.start's render
     and ack; FakeClaudeRunner vs the real runner's schema handling of `IntentReply`; load_prompt's
     substitution with `$` in Steve's text or in pantry names).
   - Trust boundaries and secrets (the bot token, raw_output, chained ValidationError text must never
     reach a reply or a `meals.*` log record; untrusted text in replies is plain text).
   - Concurrency (LatestWrites lock scope, ticket taken on the event loop, two workers, sqlite3
     check_same_thread for gate_conn vs the threads PTB uses, the claim transaction never left open).
   - Error handling gaps where failures cascade silently.
   - State machine: D5 newest-wins, D6 no write after the deadline, D7 edits.
3. For each finding give: File, Line, Category (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), Severity (CRITICAL | HIGH | MEDIUM), Bin (FIX
   NOW | DROP | LATER + reason), Finding, Evidence (a probe where cheap), Fix. Also say whether it is
   a product defect or a test-infrastructure defect, and report the split.
4. If the first pass was thorough and you find nothing material, say so plainly. Do not invent
   findings.
