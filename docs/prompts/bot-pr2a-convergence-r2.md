# Convergence round 2 — bot PR 2a (GitHub PR #38, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

Round 1 was the `/steadows-ultrareview` fleet pass (8 agents, max tier), saved at
`.context/reviews/pr38-ultrareview-r1.md`. It found **no material issues**: 0 product defects,
0 test-infrastructure defects. The done contract's stop rule is two consecutive post-PR rounds
with no product defect; this is round 2.

**Your job:** an independent second look at the same diff, not a repeat of round 1's checklist.
Try to break the three trust surfaces round 1 cleared, and check round 1's stated limitation.

- Diff: `.context/reviews/pr38.diff` (code head 0775659 on main 269009f; the PR head 9043e64 adds
  only this round's sibling brief `docs/prompts/bot-pr2a-ultrareview.md`). Commit log with
  bodies: `.context/reviews/pr38-commits.txt`. Contract: `.context/reviews/pr38-contract.md`.
  Prior gates: `.context/reviews/pr2a-code-review.md`, `.context/reviews/pr2a-verify.md`.
- Trust surfaces:
  1. Authorization — `meals/bot/gate.py` and `build_app`'s error handler `_stop_on_error` in
     `meals/bot/handlers.py`. Is there any update, ordering or exception path by which a handler
     in group 0 runs for anything but an ordinary message/edit from the allowed private chat?
  2. Secrets — can the token reach an exception's text, its rendered traceback, or a log record
     this code emits (not PTB's/httpx's own logging, which is out of scope)?
  3. Startup and PANTRY_DB — `run_bot`: refusals before polling; the once-at-startup open and
     migrate; the per-command open that must never create the file.
- Round 1's limitation: its sandbox could not create a temp directory, so the filesystem-
  dependent startup tests in `tests/test_bot/test_run_bot.py` were not run there. The lane ran
  them: 42/42 pass. Read those tests and say whether each would catch its named fault; do not
  try to run them if the sandbox refuses.
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`,
  `.github/`, `.context/reviews/pr38*`, `.context/reviews/pr2a-*`, and python-telegram-bot 22.8
  under `.venv/lib/python3.11/site-packages/telegram/`. Never read `.env`, `data/` or `seed/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) /
  DROP per `~/.claude/rules/common/done-contract.md` §2; a finding in the contract's Out of scope
  list is DROP. For each: file, line, severity, evidence, fix, and product vs test-infrastructure.
  If you find no product defect, say so plainly; do not invent findings.
