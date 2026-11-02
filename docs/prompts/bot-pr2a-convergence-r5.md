# Convergence round 5 — bot PR 2a (GitHub PR #38, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

History (reports in `.context/reviews/`):
- Round 1, the `/steadows-ultrareview` fleet: no material issue (`pr38-ultrareview-r1.md`).
- Rounds 2, 3 and 4 (`pr38-convergence-r2.md` … `-r4.md`): C1 (a logging failure opened the lock)
  fixed in 7b843be; C3 (the `is_file()` → `get_db` window can create an empty DB) → LATER,
  other-owner (contracts adds `get_db(path, create=False)`); R3-2 (test gap) fixed in d71a0b4.
  C2, R3-1, R4-1 and R4-2 were all one surface: the token reaching diagnostic text by a new route
  each round. Round 4's decision (the contract's last Amendment) removed that surface in 361ca46:
  - the error handler (`_stop_on_error` → `_format_error`) logs each exception's stack frames and
    TYPE NAME only, for the whole chain, never any exception's text; the redactor is deleted;
  - run_bot turns ANY exception from the getMe check or from run_polling into a type-only
    `RuntimeError ... from None`; InvalidToken keeps "Telegram rejected TELEGRAM_BOT_TOKEN".
  Tests 57602da.
The stop rule: two consecutive post-PR rounds with no product defect. This is the first round
after the round-4 fix. Do not re-raise C3 unless you find a different defect on that path.

**Your job:**
1. Check the round-4 decision is implemented completely: does any path in this PR's code still
   put exception text, or anything that came from Telegram or a gateway, into a run_bot refusal,
   its rendered traceback (with `__context__` / `__cause__` as Python prints them), or a
   `meals.*` log record? Remaining emitters to check: the gate's INFO drop log (chat id only),
   `_pantry`'s `logger.exception` for a failed pantry read (local SQLite errors; is anything
   Telegram-derived reachable there?), and `_pantry`'s one-line chat reply. PTB's and httpx's own
   loggers are out of scope (another lane's logging setup).
2. Check C1 still holds with the simplified `_format_error`.
3. A fresh look at the rest of the diff for anything rounds 1-4 missed.

Flag only what breaks correctness or one of the contract's ACs (`.context/reviews/pr38-contract.md`
with its Amendments). Speculative hardening and anything in its Out of scope list is DROP.

- Diff: `.context/reviews/pr38.diff` (PR head e467554; code head 361ca46; base main 269009f).
  Commit log with bodies: `.context/reviews/pr38-commits.txt`.
- The lane ran the full suite: 1204 passed, 7 deselected; tests/test_bot 68/68.
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`,
  `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`, python-telegram-bot 22.8
  under `.venv/lib/python3.11/site-packages/telegram/`, and the stdlib. Never read `.env`,
  `data/`, `seed/` or `deploy/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) /
  DROP per `~/.claude/rules/common/done-contract.md` §2. For each: file, line, severity, evidence
  (probe where cheap), fix, and product vs test-infrastructure; report the split. If you find no
  product defect, say so plainly; do not invent findings.
