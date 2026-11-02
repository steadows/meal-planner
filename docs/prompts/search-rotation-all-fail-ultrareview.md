# Post-PR ultrareview brief: search rotation-pool all-fail rule (GitHub PR #37, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL branch. Don't use
GitHub.**
- There's no GitHub access in this session, and none is needed.
- The change set is branch `feat/search-rotation-all-fail` against `origin/main` in this working tree.
- HEAD is the PR head, including this brief's own commit (docs only).

## Rules

**Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a cache under
`/tmp`, and the sandbox has denied that before. These are pre-written for you:
- `.context/reviews/search-rotation-all-fail-ultra-diff.patch`: `git diff origin/main...HEAD` for everything in the PR;
- `.context/reviews/search-rotation-all-fail-ultra-commits.txt`: the commit list;
- `.context/reviews/search-rotation-all-fail-pr-body.md`: the PR description (Acceptance → proof table, binned
  findings, Later list).

**Don't run** `uv`, `pytest`, `python`, or `claude`.
- The suite is green: 1113 passed on Python 3.11; ruff, ruff format, strict mypy and import-linter are clean.
- Reason from the code. Throwaway probes only under `/tmp`, with system tools and no repo imports.
- **A denied write is a stop-and-report, never a workaround.**

**Fleet size: Small tier (4 agents).** First pass on this PR. Trigger: about 40 changed lines of code in 2 files
(`meals/planner.py`, `tests/test_planner.py`) plus two docs briefs. No risk surface: no auth, secrets, migrations,
schema, concurrency or CI changes. If you judge a risk surface is touched, say so and size up per the skill's override.

**REVIEW ONLY.** Don't edit, create, commit or push anything. Don't spawn nested sub-fleets beyond the skill's design.

**Data boundary (docs/AUTONOMOUS_WORK.md §3).** Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`,
`CLAUDE.md`, `.context/seams/search-rotation-all-fail.md`, `.context/reviews/search-rotation-all-fail-*`. Never `.env`,
`data/`, or `seed/`. Don't contact Mealie, Telegram or Meijer.

## What the PR promises

Read `.context/seams/search-rotation-all-fail.md` and the PR body. In short: `planner._rotation_pool` raises
`MealieUnavailable` when NO rotation favourite loaded and AT LEAST ONE `get_recipe` raised `MealieUnavailable` (before:
only when every slug raised it, so a deleted favourite next to unavailable ones gave a quiet empty pool). All deleted
or none: no raise. At least one loaded: the rest are skipped with a WARNING. Message and chaining unchanged; the error
list became one `last_unavailable` variable; docstrings of `_rotation_pool` and `propose()` updated.

Context: `meals/contracts.py` (`MealieUnavailable`), `meals/fakes/mealie.py`, `meals/mealie_client.py`,
`meals/jobs.py` (`_propose`, `_catch_all`, `safe_exc_info`). @mealie's PR #36 (another branch, merging AFTER this one)
makes the real client wrap its errors in `MealieUnavailable`; treat that as a near-certain condition.

## Already found, binned (don't re-report)

- LATER, other-owner: the `MealieUnavailable` class docstring in `meals/contracts.py` says "fail loudly when every call
  fails", narrower than the new rule (contracts backlog).
- DROP: no test pins served + deleted + unavailable together.
- DROP: the real client doesn't raise yet (#36); one corrupt recipe plus stale tags fails every week (intended; a
  single always-failing favourite did so under the old rule too); the message counts deleted slugs (literally true);
  one loaded favourite turns the check off (pre-existing, Scope fence); the test doesn't vary slug order; no early stop
  after repeated timeouts (deliberate).

## Bin every finding

FIX NOW = breaks an AC, or a correctness/security/data-loss defect in code this PR adds or changes. DROP = nit, style,
speculative, pre-existing outside this surface, or in the Scope fence. LATER = a real defect that can't be fixed here,
with one reason: post-PoC, other-owner, needs-ruling or needs-design. Scope picks the bin, not severity.

## Report

The skill's report format, with a Fleet line, verified findings only, each with file, line, severity, bin and fix, and
an explicit "no verified findings" if that is the result. Classify each finding as product defect or test
infrastructure defect.
