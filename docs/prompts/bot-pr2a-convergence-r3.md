# Convergence round 3 — bot PR 2a (GitHub PR #38, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

History. Round 1 (the `/steadows-ultrareview` fleet) found no material issue
(`.context/reviews/pr38-ultrareview-r1.md`). Round 2 (single agent) found three product defects
(`.context/reviews/pr38-convergence-r2.md`):
- C1 a logging failure opened the lock → fixed in 7b843be (stop raised in `finally` in `gate` and
  `_stop_on_error`), regression tests in 88eadff;
- C2 the token leaked through Telegram failures other than InvalidToken → fixed in 7b843be
  (`_redact_token` on the secret half; every `TelegramError` at the getMe check and at polling
  start becomes a short redacted RuntimeError `from None`; the error handler logs a redacted
  formatted traceback instead of `exc_info`), tests in 88eadff;
- C3 the `is_file()` → `get_db` window can create an empty DB → binned LATER, other-owner:
  contracts agreed to add `get_db(path, create=False)` (a `mode=rw` URI open, no mkdir) in its
  next PR. Do not re-raise C3 unless you find a different defect on that path.
The contract's stop rule: two consecutive post-PR rounds with no product defect. The count
restarts here: this is the first round after the fix.

**Your job:**
1. Check that the C1 and C2 fixes are correct and complete. Try to break them: any remaining
   path where a log call or an exception lets a non-allowed update reach a group-0 handler; any
   remaining path where this code puts the token (raw, `%3A`-encoded, or another encoding a real
   proxy might produce) into an exception's text, its rendered traceback (including `__context__`
   / `__cause__`), or a `meals.*` log record. PTB's and httpx's own logging are out of scope
   (another lane's logging setup).
2. Check the new tests in 88eadff would fail if the fix were removed or weakened (e.g. a redactor
   that replaces only the exact token string, a stop raised after the log instead of in
   `finally`).
3. A fresh look at the rest of the diff for anything rounds 1 and 2 missed.

- Diff: `.context/reviews/pr38.diff` (PR head e5cdb6b; code head 7b843be; base main 269009f).
  Commit log with bodies: `.context/reviews/pr38-commits.txt`. Contract with Amendments:
  `.context/reviews/pr38-contract.md`. Prior gates: `.context/reviews/pr2a-code-review.md`,
  `.context/reviews/pr2a-verify.md`.
- The lane ran the full suite: 1185 passed, 7 deselected; tests/test_bot 49/49. If your sandbox
  can't run filesystem-dependent tests, review them statically.
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`,
  `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`, python-telegram-bot 22.8
  under `.venv/lib/python3.11/site-packages/telegram/`, and the stdlib. Never read `.env`,
  `data/`, `seed/` or `deploy/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) /
  DROP per `~/.claude/rules/common/done-contract.md` §2; a finding in the contract's Out of scope
  list is DROP. For each: file, line, severity, evidence (probe where cheap), fix, and product vs
  test-infrastructure; report the split. If you find no product defect, say so plainly; do not
  invent findings.
