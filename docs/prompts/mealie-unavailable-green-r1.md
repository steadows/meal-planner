# Mealie — GREEN repair r1: two pre-PR review gaps in get_recipe → MealieUnavailable

**Mode: implement GREEN. Single agent: no fan-out, no subagents.** You write production code that
makes a frozen, already-failing test suite pass. You do not write or change tests.

## The goal

The pre-PR code review of `HttpMealieClient.get_recipe` (your GREEN, commit d6da881) found two gaps
on this story's surface. New RED tests pin both. Close them with the smallest change.

- **F1 (AC-5):** `_parse` iterates `response.json()`. A parser answer of 200 with valid JSON that is
  not a list (`null`, `5`) raises `TypeError`, which escapes `get_recipe` raw. The contract promises
  `MealieUnavailable` for every response-body failure.
- **F2 (AC-6 d):** `_unavailable_reason` builds the message from `exc.request`. On a client that
  follows redirects, that is the LAST request in the chain, whose path the server chose through its
  `Location` header. A redirect to a path that holds the bearer token puts the token in `str(err)`
  and `repr(err)` (reproduced). The message must name the request this client built.

## Read first (required)

1. **The seam map: `.context/seams/mealie-unavailable.md`.** It is your authority. Read AC-5 and
   AC-6 (both amended today; see the parts marked "added after code review"), the Out of scope
   fence, and the Altitude decisions.
2. The review report: `.context/reviews/mealie-unavailable-code-review.md`, findings F1 and F2.
3. `meals/mealie_client.py`: `_parse`, `_unavailable_reason`, `get_recipe`, `_recipe_option`, and
   `_SLUG_RESPONSE` (the existing `TypeAdapter` pattern).
4. The frozen RED: `tests/test_mealie_client.py`, the AC-5 case
   `parser-answers-json-that-is-not-a-list` and the new AC-6 redirect test.

## Data boundary (Steve, 2026-09-25)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/` and `.context/`. Do NOT read
`.env`, anything under `data/`, or `seed/`. Those hold secrets (the Mealie token among them) and real
purchase history. Do not contact a real Mealie.

## Preflight (run first)

This repo needs no network service. It has no Postgres (nothing listens on port 5433 for it), and
the Mealie tests use `httpx.MockTransport`. Use the tools in `.venv/bin`, not `uv run`: uv's cache
may sit outside your sandbox.

```
.venv/bin/python -m pytest tests/test_mealie_client.py -q
```

Expected before you change anything: **2 failed**, every other test in the file passed. The two
failures are the F1 case (a raw `TypeError`, or "DID NOT RAISE MealieUnavailable") and the F2 test
(the token in the message, or `/api/recipes/fajitas` missing from it). If the count differs, or
anything errors on import or collection, stop and report. Do not implement.

## The task: two changes in `meals/mealie_client.py`, nothing else

1. **F1, `_parse`:** validate the parser's answer as a list through pydantic, so a wrong shape is a
   `ValidationError` (a `ValueError`, which the existing wrap already catches). Follow the
   `_SLUG_RESPONSE` pattern: a module-level `TypeAdapter(list[_Parsed])` and `validate_json` on
   `response.content`. Keep `raise_for_status()` before it, and keep the return value
   (a tuple of each item's `.ingredient`). Do not change `_ingredients` or its `zip(strict=True)`.
2. **F2, `_unavailable_reason`:** for `httpx.HTTPStatusError`, take the method and path from the
   request this client built: the first request of the redirect chain, which is
   `exc.response.history[0].request` when `exc.response.history` is not empty, else `exc.request`.
   Keep the status from `exc.response.status_code` (the final answer). Keep the fixed-fields rule:
   never `str(exc)`, `exc.args`, a header, the query or the body. Update the one-line docstring so it
   says the message names the request this client built.

Do not change `get_recipe`'s `except` clause, `_fetch_recipe`, `set_meal_plan`, `list_by_tag`,
`import_url` or `from_settings`. Add no logging, no exception class, no new module.

## Forbidden

- Any file under `tests/`. The suite is frozen. If you think a test is wrong, report it. Do not
  change it.
- `meals/contracts.py`, `meals/fakes/`, `meals/db.py`, `meals/config.py`, `meals/claude_runner.py`.
  The contracts lane owns them.
- Any file other than `meals/mealie_client.py`.
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

The orchestrator's local result after GREEN should be 0 failed, 7 deselected, and two more passed
than before the repair RED (1130 + 2). In your sandbox only, expect about 13 failures in
`tests/test_deploy.py` (`pgrep` reports "sysmond service not found", and `ps` is denied). They are not
yours. List them under "Not run by Codex" with the reason `sandbox: pgrep/ps`. The orchestrator
re-runs them. Every other test must pass.

## Report

1. The changed file with a short summary of each change.
2. The pytest summary line, and the result of each of the other four checks.
3. **Not run by Codex:** list every test that was skipped, deselected or failed for a sandbox
   reason, each with its reason (`opt-in live flag` for the `integration` marker,
   `sandbox: pgrep/ps` for the deploy tests, or `out of scope`). Write "none" if there are none. A
   skip or failure with no reason counts as unverified.
4. Anything in the seam map, the review report or the frozen tests that you think is wrong. Report
   it. Do not act on it.
