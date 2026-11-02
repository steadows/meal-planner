# Pantry PR 2b — GREEN: the F2 week floor, real half

**Mode: implement GREEN. Single agent: no fan-out, no subagents.** You write production code that
makes a frozen, already-failing test suite pass. You do not write or change tests.

## The goal

Steve ruled on 2026-11-01 that "still good" always holds the next pantry question off for at least
a week. The contracts lane changed the reference (the fake pantry and the `Pantry` Protocol text).
Make the real pantry (`meals/pantry.py`) match it.

## Read first (required)

1. **The seam map: `.context/seams/lane-b-pantry.md`, section "Revision 7".** It is your
   authority: the rule, the seams, the done contract (AC-1 to AC-7) and the out-of-scope fence.
   Revision 7 wins over every revision below it.
2. `meals/contracts.py`: the `Pantry` class docstring and `Pantry.confirm_stocked`'s docstring.
   This is the Protocol text, already changed by contracts for the floor.
3. `meals/fakes/pantry.py`: `_ask_date` and `FakePantry.confirm_stocked`. This is the reference
   behaviour. The real pantry must match it exactly. `tests/test_pantry_parity.py` compares the two
   item by item.
4. `meals/pantry.py`: `_ninety_percent_point`, `_ask_date`, `_postponed_ask`,
   `SqlitePantry.confirm_stocked`, `staples_due`, `_due_sort_key`.
5. `meals/mcp_tools.py`: the `confirm_stocked` tool.
6. The frozen RED: `tests/test_pantry_confirm.py` and `tests/test_pantry_parity.py`.

## Data boundary (Steve, 2026-09-25)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT read `.env`,
anything under `data/`, or `seed/`. Those hold secrets and real purchase history.

## Preflight (run first)

This repo uses SQLite only, in memory in the tests. It needs no network service. Use the tools in
`.venv/bin`, not `uv run`: uv's cache may sit outside your sandbox.

```
.venv/bin/python -m pytest tests/test_pantry_confirm.py tests/test_pantry_parity.py -q
```

Expected before you change anything: 9 failed. That is 8 in `test_pantry_confirm.py` plus
`test_the_fake_and_the_real_pantry_agree_on_the_same_replies`. Each one fails on an assertion. If
the count differs, or anything errors on import or setup, stop and report. Do not implement.

## The rule

- `pushed = on + (max(interval, 7) if plenty and interval is not None else 7)`. Unchanged.
- `confirm_stocked` stores `max(item.next_ask_on, pushed)`, or `pushed` when `next_ask_on` is
  None. It always stores a date.
- The ask date is the later of `next_ask_on` and the 90% point, whichever are known. None when
  neither is known.

## The task: four changes, nothing else

1. `meals/pantry.py` `_ask_date(item) -> date | None`: the later of the known dates. Update its
   docstring to say so.
2. `meals/pantry.py` `_postponed_ask(item, on, plenty) -> date`: the return type narrows to
   `date`. Return `max(pushed, item.next_ask_on)` when `next_ask_on` is set, else `pushed`. Keep
   its docstring short and pointing at `confirm_stocked`'s, as it does today.
3. `SqlitePantry.confirm_stocked` docstring (review finding F5): mirror the Protocol's
   `confirm_stocked` docstring in full. Adapt it only where the Protocol talks about the interface
   rather than this class. The SQL statement and the log line stay as they are. The log line
   already reports `_ask_date(updated)`.
4. `meals/mcp_tools.py`, the `confirm_stocked` tool docstring (review finding F3). The model reads
   this text as the tool description. Say the floor plainly: the next ask waits at least a week, or
   one interval for "plenty" (never less than a week), and an ask date already set later than that
   stands. Keep the sentence about `on`. Keep it as short as it is now.

`_due_sort_key` and `staples_due` call `_ask_date`, so they pick up the rule with no change. Add
no helper, constant or module.

## Forbidden

- Any file under `tests/`. The suite is frozen. If you think a test is wrong, report it. Do not
  change it.
- `meals/contracts.py`, `meals/fakes/`, `meals/db.py`, `meals/config.py`, `meals/claude_runner.py`.
  The contracts lane owns them.
- `pytest -m integration`. That marker calls live `claude -p` and real services.
- git. You can't run it in this sandbox, and the orchestrator commits.

## Done when

All of these pass, run from the worktree root:

```
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports
```

Expected in your sandbox only: about 13 failures in `tests/test_deploy.py` (`pgrep` reports
"sysmond service not found", and `ps` is denied). They are not yours. List them under "Not run by
Codex" with the reason `sandbox: pgrep/ps`. The orchestrator re-runs them. Every other test must
pass.

## Report

1. Each changed file with a one-line summary of the change.
2. The pytest summary line, and the result of each of the other four checks.
3. **Not run by Codex:** list every test that was skipped, deselected or not run, each with its
   reason (`opt-in live flag` for the `integration` marker, `sandbox: pgrep/ps` for the deploy tests, or
   `out of scope`). Write "none" if
   there are none. A skip with no reason counts as unverified.
4. Anything in Revision 7, the Protocol text or the frozen tests that you think is wrong. Report
   it. Do not act on it.
