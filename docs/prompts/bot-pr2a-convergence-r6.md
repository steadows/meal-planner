# Convergence round 6 — bot PR 2a (GitHub PR #38, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

History (reports in `.context/reviews/`, contract with Amendments in `pr38-contract.md`):
- Round 1, the `/steadows-ultrareview` fleet: no material issue.
- Rounds 2-4: C1 (a logging failure opened the lock) fixed in 7b843be; R3-2 (test gap) fixed in
  d71a0b4; C3 (the `is_file()` → `get_db` window can create an empty DB) → LATER, other-owner
  (contracts adds `get_db(path, create=False)`). C2, R3-1, R4-1, R4-2 were one surface (the token
  in diagnostic text); 361ca46 removed it: the error handler logs stack frames and type names
  only, and run_bot turns any exception from getMe or run_polling into a type-only refusal.
- Round 5 (`pr38-convergence-r5.md`): one finding, R5-1 — the gate's INFO drop log prints
  `chat.id` unvalidated, so a forged getUpdates reply could put the token there. The lane binned
  it DROP (speculative): only a party that already holds the token (Telegram, or a TLS
  man-in-the-middle who sees it in every request URL) can forge that reply, so the log line
  exposes nothing new to it, and a benign proxy's error page fails before dispatch. **You may
  challenge that bin**, but only with a concrete path by which a party that does NOT already hold
  the token causes the token (or other secret) to reach a `meals.*` log record or a refusal.

The stop rule: two consecutive post-PR rounds with no FIX NOW product defect. Round 5 had none
after binning; this is round 6, the second. If it is clean, the review stops and the rest is
recorded residual risk.

**Your job:** an independent final pass over the whole diff at PR head (code head 361ca46).
Look for anything that breaks correctness or one of the contract's ACs (AC-1 chat lock, AC-13
/pantry, AC-16 startup refusal, as amended). Do not re-raise C3 or the round-1-to-5 items unless
a fix is wrong. Speculative hardening, anything requiring an attacker who already holds the
token, and anything in the contract's Out of scope list is DROP.

- Diff: `.context/reviews/pr38.diff`; commit log with bodies: `.context/reviews/pr38-commits.txt`.
- The lane ran the full suite: 1204 passed, 7 deselected; tests/test_bot 68/68.
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`,
  `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`, python-telegram-bot 22.8
  under `.venv/lib/python3.11/site-packages/telegram/`, and the stdlib. Never read `.env`,
  `data/`, `seed/` or `deploy/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) /
  DROP per `~/.claude/rules/common/done-contract.md` §2. For each: file, line, severity, evidence
  (probe where cheap), fix, and product vs test-infrastructure; report the split. If you find no
  product defect, say so plainly; do not invent findings.
