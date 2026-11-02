# Post-PR ultrareview brief: search rotation-pool skip (GitHub PR #32, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL branch. Don't use
GitHub.**
- There's no GitHub access in this session, and none is needed.
- The change set is branch `feat/search-rotation-skip` against `origin/main` in this working tree.
- HEAD is the PR head, including this brief's own commit (docs only).

## Rules

**Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a cache under
`/tmp`, and the sandbox has denied that before. These are pre-written for you:
- `.context/reviews/search-rotation-skip-ultra-diff.patch`: `git diff origin/main...HEAD` for everything in the PR;
- `.context/reviews/search-rotation-skip-ultra-commits.txt`: the commit list;
- `.context/reviews/search-rotation-skip-pr-body.md`: the PR description (Acceptance → proof table, binned findings,
  Later list).

**Don't run** `uv`, `pytest`, `python`, or `claude`.
- The suite is green: 1113 passed on Python 3.11; ruff, ruff format, strict mypy and import-linter are clean.
- Reason from the code.
- Throwaway probes are allowed only under `/tmp`, using system tools with no repo imports.
- **A denied write is a stop-and-report, never a workaround.**

**Fleet size: Small tier (4 agents).** This is the FIRST pass on this PR, so a full fleet for the tier is allowed.
Trigger: about 88 changed lines of code in 3 files (`meals/planner.py`, `tests/test_planner.py`,
`tests/test_search.py`) plus this docs brief and the pre-PR sweep brief. No risk surface: no auth, secrets,
migrations, schema, concurrency or CI changes. The one security-adjacent rule (log a `MealieUnavailable`'s message,
never its traceback) is checked by a test. If you judge a risk surface is touched after all, say so and size up per
the skill's override.

**REVIEW ONLY.** Don't edit, create, commit or push anything in the repo. Don't spawn nested sub-fleets beyond the
skill's own design.

**Data boundary (docs/AUTONOMOUS_WORK.md §3).** Read only:
- `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`;
- `CLAUDE.md`, `.context/seams/search-rotation-skip.md`, `.context/reviews/search-rotation-skip-*`.
Never read `.env`, `data/`, or `seed/`. Don't contact Mealie, Telegram or Meijer.

## What the PR promises

Read `.context/seams/search-rotation-skip.md` (done contract, Scope fence, altitude decisions) and the PR body. In
short:
- `planner._rotation_pool` skips a favourite whose `get_recipe` raises `contracts.MealieUnavailable`, with a WARNING
  naming the slug (message only).
- When every `get_recipe` raised `MealieUnavailable`, it raises `MealieUnavailable` naming the count, chained from
  the last one, before any Claude run. A `KeyError` (unknown slug) never counts.
- search's two live-Claude tests opt in via the `live_claude` fixture (`MEALS_LIVE_CLAUDE=1`).

Context you will need: `meals/contracts.py` (`MealieUnavailable`, `MealieClient`), `meals/fakes/mealie.py`,
`meals/mealie_client.py`, `meals/jobs.py` (`_propose`, `_catch_all`, `safe_exc_info`), `tests/conftest.py`.

## Already found, binned (don't re-report)

- FIX NOW, fixed (e76cbd8): docstrings said "a KeyError is Mealie answering 404"; now "an unknown slug".
- DROP: the real `HttpMealieClient` doesn't raise `MealieUnavailable` yet (@mealie's wrap, in progress on another
  branch; Scope fence). Treat it as a near-certain future condition when you judge the code.
- DROP: a partial outage only shrinks the pool with WARNINGs (deliberate; the contract asks for a loud failure when
  every call fails).
- DROP: no early stop after a streak of failures (changes the agreed rule; weekly job).
- LATER, other-owner: `list_by_tag` failures raise raw httpx errors, not `MealieUnavailable` (Scope fence).

## Bin every finding

Use `~/.claude/rules/common/done-contract.md` §2 if you can read it; otherwise: FIX NOW = breaks an AC, or a
correctness/security/data-loss defect in code this PR adds or changes. DROP = nit, style, speculative, pre-existing
outside this surface, or in the Scope fence. LATER = a real defect that can't be fixed here, with one reason:
post-PoC, other-owner, needs-ruling or needs-design. Scope picks the bin, not severity.

## Report

The skill's report format, with a Fleet line, verified findings only, each with file, line, severity, bin and fix,
and an explicit "no verified findings" if that is the result. Also classify each finding as product defect or test
infrastructure defect.
