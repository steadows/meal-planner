# Convergence round 4 — bot PR 2a (GitHub PR #38, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

History (reports in `.context/reviews/`):
- Round 1, the `/steadows-ultrareview` fleet: no material issue (`pr38-ultrareview-r1.md`).
- Round 2: C1 (a logging failure opened the lock) and C2 (the token leaked through non-InvalidToken
  Telegram errors) → fixed in 7b843be, tests 88eadff; C3 (the `is_file()` → `get_db` window can
  create an empty DB) → LATER, other-owner: contracts adds `get_db(path, create=False)`
  (`pr38-convergence-r2.md`).
- Round 3: R3-1 (the redactor missed a token encoded inside the secret, `%2D` / `&#45;`) and R3-2
  (C1's test never exercised the error handler's own `finally`) (`pr38-convergence-r3.md`).
  R3-1 was fixed at the root in 52a6c51: Telegram's response text is untrusted and is never
  emitted. run_bot's refusals name the error type only; the error handler renders the exception
  chain itself (`_format_error`: stack frames, the type name for a `TelegramError`, type and
  message with the secret half removed for our own exceptions). Tests d71a0b4.
The stop rule: two consecutive post-PR rounds with no product defect. This is the first round
after the round-3 fix. Do not re-raise C3 unless you find a different defect on that path.

**Your job:**
1. Check the R3-1 fix holds: is there any remaining path where text that came from Telegram or
   a gateway (a `TelegramError`'s message, its causes or contexts, anything PTB copied from a
   response body) reaches a run_bot refusal, its rendered traceback, or a `meals.*` log record?
   Check `_format_error`'s chain walk against how Python prints chains (cause vs context vs
   suppressed context, cycles), and what a non-Telegram exception in that chain could carry.
   PTB's and httpx's own loggers are out of scope (another lane's logging setup).
2. Check C1 still holds with the new `_format_error` (a failure inside the formatter itself must
   not stop the `finally` from stopping the update).
3. A fresh look at the rest of the diff for anything rounds 1-3 missed.

- Diff: `.context/reviews/pr38.diff` (PR head 2b8b133; code head 52a6c51; base main 269009f).
  Commit log with bodies: `.context/reviews/pr38-commits.txt`. Contract with Amendments:
  `.context/reviews/pr38-contract.md`.
- The lane ran the full suite: 1192 passed, 7 deselected; tests/test_bot 56/56.
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`,
  `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`, python-telegram-bot 22.8
  under `.venv/lib/python3.11/site-packages/telegram/`, and the stdlib. Never read `.env`,
  `data/`, `seed/` or `deploy/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) /
  DROP per `~/.claude/rules/common/done-contract.md` §2; a finding in the contract's Out of scope
  list is DROP. For each: file, line, severity, evidence (probe where cheap), fix, and product vs
  test-infrastructure; report the split. Flag only what affects correctness or the contract's
  ACs. If you find no product defect, say so plainly; do not invent findings.
