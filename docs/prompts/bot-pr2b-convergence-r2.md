# Convergence round 2 — bot PR 2b (GitHub PR #40, steadows/meal-planner)

You are ONE reviewer working alone. **Do not spawn subagents or a fleet.** Review only: do not
edit, create or delete any repo file; do not commit, push, run git or use GitHub.

History (reports in `.context/reviews/`, contract in `pr40-contract.md`):
- Pre-PR: `/steadows-code-review` (`pr2b-code-review.md`; CR-1 and CR-2 fixed in e114ad7 + b7b143b;
  its DROP, LATER and filtered lists) and `/steadows-verify` (`pr2b-verify.md`, READY).
- Round 1, the `/steadows-ultrareview` fleet (`pr40-ultrareview-r1.md`): 0 product, 0
  test-infrastructure defects. One DROP: edited media captions are outside 2b's text scope.

The stop rule: two consecutive post-PR rounds with no FIX NOW product defect. Round 1 had none; this
is round 2. If it is clean, the review stops and the rest is recorded residual risk.

**Your job:** an independent final pass over the whole diff at PR head d9da717. Look for anything
that breaks correctness or one of this PR's ACs (AC-2 claim failure, AC-3, AC-4, AC-6, AC-7, AC-8,
AC-9, AC-11, AC-12, the run_bot gate connection). Do not re-raise an item already binned in the
reports above unless its fix or bin is wrong, with concrete evidence. Speculative hardening, items
routed to other lanes (background.py exception text and httpx INFO logging → wiring P4.1;
claude_runner slot wait, reason text, Text control characters → contracts' hardening PR; certifi and
opentelemetry → pm #33) and anything in the contract's Out of scope list are DROP.

- Diff: `.context/reviews/pr40.diff`; commit log with bodies: `.context/reviews/pr40-commits.txt`;
  PR description: `.context/reviews/pr2b-body.md`.
- The lane ran the full suite: 1303 passed, 8 deselected (opt-in integration); tests/test_bot
  112/112. Your sandbox may block pytest's temporary directories; if so, review statically and use
  `.venv/bin/python -c '...'` probes (in-memory SQLite, `FakeClaudeRunner`, `FakePantry`).
- Data boundary: read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.github/`,
  `.context/reviews/pr40*`, `.context/reviews/pr2b-*`, python-telegram-bot 22.8 under
  `.venv/lib/python3.11/site-packages/telegram/`, and the stdlib. Never read `.env`, `data/`,
  `seed/` or `deploy/`.
- Bin each finding FIX NOW / LATER (post-PoC · other-owner · needs-ruling · needs-design) / DROP per
  `~/.claude/rules/common/done-contract.md` §2. For each: file, line, severity, evidence (probe where
  cheap), fix, and product vs test-infrastructure; report the split. If you find no product defect,
  say so plainly; do not invent findings.
