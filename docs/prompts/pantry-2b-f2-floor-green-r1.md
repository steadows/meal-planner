# Pantry PR #34 — GREEN, review round 1: compute the logged ask date before the write commits

**Mode: implement GREEN. Single agent: no fan-out, no subagents.** You change production code to
make one frozen, failing test pass. You do not write or change tests.

## Read first (required)

1. The seam map: `.context/seams/lane-b-pantry.md`, section "Revision 7". It is the authority for
   the week-floor rule this PR ships.
2. `meals/pantry.py`: `_ninety_percent_point`, `_ask_date`, `_postponed_ask`, `SqlitePantry._write`
   and `SqlitePantry.confirm_stocked`.
3. The frozen RED test: the newest test in `tests/test_pantry_confirm.py` that names the
   ultrareview finding (it confirms an item with `last_purchased=date.max`).

## Data boundary (Steve, 2026-09-25)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT read `.env`,
`data/` or `seed/`.

## The finding (post-PR ultrareview, LOW, FIX NOW)

`SqlitePantry.confirm_stocked` computes `_ask_date(updated)` for its log line after the
`with self._write():` block has committed. For an item whose 90% point overflows the date range
(`typical_interval_days=1`, `last_purchased=date.max`), the UPDATE commits and then the call raises
`OverflowError`. The caller sees a failure, but the row was changed. The old code raised before it
wrote.

## Preflight (run first)

This repo uses SQLite only, in memory in the tests. It needs no network service. Use the tools in
`.venv/bin`, not `uv run`.

```
.venv/bin/python -m pytest tests/test_pantry_confirm.py -q
```

Expected before you change anything: exactly 1 failed (the new test), on its assertion. If the
count differs, or anything errors on import or setup, stop and report. Do not implement.

## The task: one change

In `SqlitePantry.confirm_stocked`, compute the effective ask date inside the `with self._write():`
block, after the re-read, so an exception there rolls the write back. Log it after the block, as
now. Keep the log line's text and both values (the effective ask date and the stored floor). Add
no helper. Change nothing else.

## Forbidden

- Any file under `tests/`. The suite is frozen.
- `meals/contracts.py`, `meals/fakes/`, `meals/db.py`, `meals/config.py`, `meals/claude_runner.py`.
- `pytest -m integration`.
- git. You can't run it in this sandbox, and the orchestrator commits.

## Done when

```
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports
```

Every test passes. `tests/test_deploy.py` is not on this branch, so no sandbox failures are
expected. If any appear, list them below with their reason.

## Report

1. The changed lines, in one sentence.
2. The pytest summary line, and the result of each of the other four checks.
3. **Not run by Codex:** every test that was skipped, deselected or not run, each with its reason
   (`opt-in live flag` for the `integration` marker, or `out of scope`). Write "none" if none.
4. Anything you think is wrong in the test or the finding. Report it. Do not act on it.
