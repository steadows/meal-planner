# Convergence pass 1 — PR #36 (get_recipe → MealieUnavailable): ultrareview findings U1 and U2

**Single agent. Do NOT spawn sub-agents or fan out; this is a convergence pass, not a fleet.
REVIEW ONLY: do not edit, commit or push anything.**

git is not available to you here, so the diffs are pre-written:
- `.context/reviews/mealie-unavailable-r2-fix.diff` — the repair: `git diff cce2aad..b4493f1`
  (repair RED `52c349a` + repair GREEN `b4493f1`).
- `.context/reviews/mealie-unavailable.diff` — the whole PR, `git diff origin/main...HEAD` at b4493f1.

The working tree is at the PR head, so read any file in full from it.

## Data boundary (docs/AUTONOMOUS_WORK.md §3)

Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, `.context/seams/` and
`.context/reviews/`. Never read `.env`, `data/` or `seed/`. Never contact a real Mealie, Telegram or
Meijer. Do not run `pytest -m integration`. Use `.venv/bin/python -m pytest`, `.venv/bin/ruff`,
`.venv/bin/mypy` and short `.venv/bin/python -c` probes; write no files.

## The findings to re-check (from your ultrareview round 1, `.context/reviews/mealie-unavailable-ultrareview-r1.md`)

- **U1:** a 3xx with a `Location` httpx can't parse raised raw `httpx.InvalidURL` from `get_recipe`.
  Fix: `httpx.InvalidURL` added to `get_recipe`'s `except` tuple.
- **U2:** a recipe body nested ~1100 deep raised raw `RecursionError` from `response.json()` in
  `_fetch_recipe`. Fix: `_fetch_recipe` reads with `_Recipe.model_validate_json(response.content)`.
- U3 (opaque token as slug) was binned DROP: Mealie issues JWTs, whose dots fail the slug allowlist,
  and the planner already logs the slug itself (pre-existing). Re-open it only if that reasoning is
  wrong.

The contract is `.context/seams/mealie-unavailable.md` (AC-4 and AC-5 amended for U1/U2; AC-7 and AC-8
unchanged; the Out of scope fence applies).

## Your mandate

1. For U1 and U2: is the fix correct and complete? Probe it (the original reproductions, plus the
   same failure on the parser POST for U1, and a deeply nested parser answer for U2).
2. Did the fix introduce a new defect? In particular:
   - Does catching `httpx.InvalidURL` wrap anything that is really a programming error (AC-8)?
   - Does `model_validate_json` change `_fetch_recipe`'s behaviour for a valid Mealie recipe body
     in a way the old `model_validate(response.json())` did not (encoding, UUID or number parsing,
     extra fields), for `get_recipe` or for `set_meal_plan` (AC-7)?
   - Can the token now reach `str(err)` or `repr(err)` by any new path?
3. Report each item with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), bin (FIX NOW · DROP ·
   LATER + reason, per `~/.claude/rules/common/done-contract.md` §2), whether it is a product or a
   test-infrastructure defect, and evidence. If U1 and U2 are closed and nothing new is found, say
   exactly: "Convergence: no product defect."
