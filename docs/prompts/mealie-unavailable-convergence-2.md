# Convergence pass 2 — PR #36 (get_recipe → MealieUnavailable), at the merged head

**Single agent. Do NOT spawn sub-agents or fan out; this is a convergence pass, not a fleet.
REVIEW ONLY: do not edit, commit or push anything.**

Pass 1 (`docs/prompts/mealie-unavailable-convergence.md`) found no product defect: U1 and U2 closed,
one DROP (non-UTF-8 bodies). Since then the branch merged main at `0cd574d`, which brings in three
other lanes' PRs: #37 (search: `_rotation_pool` now raises when nothing loaded and any favourite was
unavailable), #35 (contracts: the `MealieClient.get_recipe` docstring now points at the
`MealieUnavailable` class docstring), and #34 (pantry, unrelated). The PR's own diff is unchanged.

git is not available to you here, so the diffs are pre-written:
- `.context/reviews/mealie-unavailable.diff` — the whole PR, `git diff origin/main...HEAD` at `0cd574d`.
- `.context/reviews/mealie-unavailable-main-since-base.diff` — what main changed since this branch
  was cut (`32fbfda..origin/main`) in `meals/planner.py`, `meals/contracts.py` and `meals/jobs.py`.

The working tree is at the merged head, so read any file in full from it.

## Data boundary (docs/AUTONOMOUS_WORK.md §3)

Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, `.context/seams/` and
`.context/reviews/`. Never read `.env`, `data/` or `seed/`. Never contact a real Mealie, Telegram or
Meijer. Do not run `pytest -m integration`. Use `.venv/bin/python -m pytest`, `.venv/bin/ruff`,
`.venv/bin/mypy` and short `.venv/bin/python -c` probes; write no files.

## Your mandate

1. Re-confirm U1 and U2 stay closed at this head, and that AC-1 to AC-8 in
   `.context/seams/mealie-unavailable.md` still hold (`.venv/bin/python -m pytest tests/test_mealie_client.py tests/test_planner.py -q`).
2. Cross-file, now that the consumers changed:
   - With #37's guard, does `propose()` now fail loudly, with a log-safe message and no traceback
     that carries the token, when the real client's `get_recipe` raises `MealieUnavailable` for
     every favourite, and when some favourites are deleted (`KeyError`) and the rest unavailable?
     Probe it with the real `HttpMealieClient` over `httpx.MockTransport`, not the fake.
   - Does `meals/jobs.py` (`safe_exc_info`, `_catch_all`) still hide the traceback for that
     chain, and is anything the message carries unsafe to send to Telegram?
   - Does #35's Protocol docstring now match what `HttpMealieClient.get_recipe` does
     (`KeyError` / `MealieUnavailable` / programming errors escape)?
3. Report each item with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), bin (FIX NOW · DROP ·
   LATER + reason, per `~/.claude/rules/common/done-contract.md` §2), whether it is a product or a
   test-infrastructure defect, and evidence. If nothing new is found, say exactly:
   "Convergence: no product defect."
