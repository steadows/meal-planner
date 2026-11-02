# Post-PR ultrareview brief — bot PR 2b (GitHub PR #40, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
working tree. Do not use GitHub, and do not run git.** Neither is available in this session, and
neither is needed: the PR's diff against main is pre-written at `.context/reviews/pr40.diff`
(branch `feat/bot-free-text`, head d9da717, on main 28426e2, where #38 merged), the commit log with
bodies at `.context/reviews/pr40-commits.txt`, and every reviewed file is in the tree as committed.
The PR description is `.context/reviews/pr2b-body.md` (verbatim as posted).

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill. Treat these as higher-risk, which forces a full-size fleet:
  - concurrency and at-most-once delivery: the update_id claim (`meals/bot/gate.py` `claim`, one
    transaction: prune older than 48 h, then `INSERT ... ON CONFLICT DO NOTHING`, never left open)
    and newest-wins by arrival ticket (`meals/bot/pantry_ops.py` `LatestWrites`: ticket taken on the
    event loop before `background.start`; one lock covers check, write and record; no write after
    `apply_by`).
  - authorization: the chat lock (from #38) must still run before the claim and before every new
    handler (free text, edited text); edited messages must never write.
  - secrets and untrusted text: the bot token, `ClaudeRunnerError.raw_output` and chained
    validation text must never reach a reply or a `meals.*` log record; Steve's text goes into a
    Claude prompt (`meals/prompts/intents/parse.md`) run with no tools (`web=False`), and only
    `pantry_flip → buy_next_time` writes.
- **REVIEW ONLY:** do not edit, create or delete any repo file, and do not commit or push.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `uv.lock`, `.github/`, `.context/reviews/pr40*`, `.context/reviews/pr2b-*`,
  and python-telegram-bot 22.8 under `.venv/lib/python3.11/site-packages/telegram/`. Never read
  `.env` (it links to real secrets), `data/`, `seed/` or `deploy/`, or any real Telegram, Mealie or
  Meijer content. Run tests with `.venv/bin/python -m pytest tests/test_bot -q -p no:cacheprovider`.
- **The done contract:** `.context/reviews/pr40-contract.md` (Done means, the AC table, Out of
  scope, Budget, D1-D8, F2, "Carried into 2b", PR split rev 4, and the last sections, which win
  where they differ). This PR proves AC-2 (claim failure), AC-3, AC-4, AC-6, AC-7, AC-8, AC-9,
  AC-11, AC-12 and the run_bot gate connection; the done check's table is
  `.context/reviews/pr2b-done-check.md`. Bin every finding FIX NOW / LATER (post-PoC · other-owner ·
  needs-ruling · needs-design) / DROP per `~/.claude/rules/common/done-contract.md` §2. A finding in
  the Out of scope list is DROP: one line, no argument.
- **Prior gates, so you don't re-raise what is already fixed or binned (unless the fix or bin is
  wrong, with concrete evidence):**
  - Pre-PR code review: `.context/reviews/pr2b-code-review.md` (CR-1 and CR-2 fixed; its DROP,
    LATER and filtered lists) and its Codex sweep brief `docs/prompts/bot-pr2b-adversarial-review.md`.
  - `/steadows-verify`: `.context/reviews/pr2b-verify.md` (READY; its LATER and DROP lists).
  - Known and routed elsewhere: `meals/background.py` logging and replying with exception text,
    and httpx logging the token-bearing URL at INFO, are wiring's (P4.1 preconditions); the
    `claude_runner` slot wait, the validation reason's property names and `Text` inner control
    characters are in contracts' hardening PR; certifi/opentelemetry are pm's PR #33.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), bin, evidence (run
  probes where cheap: an in-process PTB `Application` with a fake `BaseRequest` works, see
  `tests/test_bot/conftest.py`; `FakeClaudeRunner` and `FakePantry` are in `meals/fakes/`) and a
  concrete fix. Classify each finding as a product defect or a test-infrastructure defect, and
  report the split. Say explicitly if nothing material is found.
