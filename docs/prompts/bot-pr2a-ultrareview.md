# Post-PR ultrareview brief — bot PR 2a (GitHub PR #38, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
working tree. Do not use GitHub, and do not run git.** Neither is available in this session, and
neither is needed: the PR's diff against main is pre-written at `.context/reviews/pr38.diff`
(branch `feat/bot-skeleton`, code head 0775659, on main 269009f), the commit log with bodies at
`.context/reviews/pr38-commits.txt`, and every reviewed file is in the tree as committed. The PR
description is `.context/reviews/pr2a-body.md` (verbatim as posted).

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill. Treat these as higher-risk, which forces a full-size fleet:
  - authorization: the chat lock is the bot's only access control. One blocking `TypeHandler` in
    group -1 (`meals/bot/gate.py`) plus an application error handler that stops the update
    (`meals/bot/handlers.py`, `_stop_on_error`).
  - secrets: the bot token must never appear in an exception's text, its rendered traceback, or a
    log record this code emits; `run_bot` checks the token with one getMe first.
  - startup and the pantry database: `run_bot` opens `PANTRY_DB` once (validate + migrate) and
    each `/pantry` opens it again without ever creating it.
- **REVIEW ONLY:** do not edit, create or delete any repo file, and do not commit or push.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `uv.lock`, `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`,
  and python-telegram-bot 22.8 under `.venv/lib/python3.11/site-packages/telegram/`. Never read
  `.env` (it links to real secrets), `data/` or `seed/`, or any real Telegram, Mealie or Meijer
  content. Run tests with `.venv/bin/python -m pytest tests/test_bot -q`.
- **The done contract:** `.context/reviews/pr38-contract.md` (Done means, AC-1, AC-13, AC-16,
  Out of scope, Budget, Amendments). Bin every finding FIX NOW / LATER (post-PoC · other-owner ·
  needs-ruling · needs-design) / DROP per `~/.claude/rules/common/done-contract.md` §2. A finding
  in the Out of scope list is DROP: one line, no argument.
- **Prior gates, so you don't re-raise what is already fixed (unless the fix is wrong):**
  - Pre-PR code review: `.context/reviews/pr2a-code-review.md` (F1-F7, with its DROP list) and
    its Codex sweep brief `docs/prompts/bot-pr2a-adversarial-review.md`.
  - `/steadows-verify`: `.context/reviews/pr2a-verify.md` (security stage S1-S3, fixed; its
    LATER and DROP lists).
  - Known and routed elsewhere: bot logging setup (httpx logs the token-bearing URL at INFO;
    PTB's network loop logs a rejected token at ERROR) is wiring P4.1's; the certifi advisory is
    pm's PR #33.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), bin, evidence (run
  probes where cheap: an in-process PTB `Application` with a fake `BaseRequest` works, see
  `tests/test_bot/conftest.py`) and a concrete fix. Classify each finding as a product defect or
  a test-infrastructure defect, and report the split. Say explicitly if nothing material is found.
