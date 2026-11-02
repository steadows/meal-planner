# Mealie — GREEN: get_recipe raises MealieUnavailable (error-contract step 2)

**Mode: implement GREEN. Single agent: no fan-out, no subagents.** You write production code that
makes a frozen, already-failing test suite pass. You do not write or change tests.

## The goal

`HttpMealieClient.get_recipe` must raise `KeyError` only when Mealie has no such recipe, and
`contracts.MealieUnavailable` for every other HTTP, network or response-body failure. Raise it
`from` the original error. Build its message from fixed fields only, so the Mealie bearer token can
never appear in `str(err)` or `repr(err)`. The planner (`meals/planner.py` `_rotation_pool`, already
on main) catches `MealieUnavailable`, skips that favourite and logs `str(err)`. This change makes
that work against the real Mealie.

## Read first (required)

1. **The seam map: `.context/seams/mealie-unavailable.md`.** It is your authority: the done
   contract (AC-1 to AC-8), the out-of-scope fence, the reuse table, the new seams and their exact
   shapes, and the altitude decisions. Follow its "New seams" section.
2. `meals/contracts.py`: `class MealieUnavailable` and its docstring, and the `MealieClient`
   Protocol's `get_recipe` docstring.
3. `meals/mealie_client.py`: `get_recipe`, `_fetch_recipe`, `_ingredients`, `_parse`, `set_meal_plan`.
4. `meals/fakes/mealie.py`: `FakeMealieClient.get_recipe`, the reference for the error type.
5. The frozen RED: `tests/test_mealie_client.py`, the tests whose names contain
   `mealie_unavailable`, `programming_error` or `still_raises_http_status_error`.

## Data boundary (Steve, 2026-09-25)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT read `.env`,
anything under `data/`, or `seed/`. Those hold secrets (the Mealie token among them) and real
purchase history. Do not contact a real Mealie.

## Preflight (run first)

This repo needs no network service. It has no Postgres (nothing listens on port 5433 for it), and
the Mealie tests use `httpx.MockTransport`. Use the tools in `.venv/bin`, not `uv run`: uv's cache
may sit outside your sandbox.

```
.venv/bin/python -m pytest tests/test_mealie_client.py -q
```

Expected before you change anything: **15 failed, 145 passed**. Each failure is either "DID NOT
RAISE MealieUnavailable" or a raw `HTTPStatusError` / `RequestError` / `ValidationError` /
`JSONDecodeError` / `ValueError` escaping. If the count differs, or anything errors on import or
collection, stop and report. Do not implement.

## The task: one file, three pieces

All in `meals/mealie_client.py`:

1. **`_recipe_option(self, slug: str) -> RecipeOption`** (private method, new). Move the current
   body of `get_recipe` into it verbatim, with its comment about `contracts.TRUSTED`.
2. **`get_recipe`** becomes the wrap:
   ```python
   def get_recipe(self, slug: str) -> RecipeOption:
       try:
           return self._recipe_option(slug)
       except (httpx.HTTPError, ValueError) as exc:  # KeyError (no such recipe) passes through
           raise MealieUnavailable(_unavailable_reason(slug, exc)) from exc
   ```
   - Catch exactly `httpx.HTTPError` and `ValueError`. Not `Exception`: a programming error such as
     a `TypeError` must escape unchanged (AC-8). Not a list of `ValueError` subclasses: pydantic's
     `ValidationError`, `json.JSONDecodeError` and `UnicodeDecodeError` are all `ValueError`s. A
     short comment may name them.
   - `KeyError` must not be caught (AC-1).
   - Import `MealieUnavailable` from `meals.contracts` on the existing import line.
3. **`_unavailable_reason(slug: str, exc: Exception) -> str`** (module-level private function, new).
   - For `httpx.HTTPStatusError`: name the status code, the request method and the request path,
     e.g. `f"Mealie couldn't serve recipe {slug}: HTTP {status} from {method} {path}"` using
     `exc.response.status_code`, `exc.request.method`, `exc.request.url.path`. Use the path only:
     never the full URL, the query, a header, or the response body.
   - Otherwise: `f"Mealie couldn't serve recipe {slug}: {type(exc).__name__}"`.
   - **Never** use `str(exc)`, `repr(exc)`, `exc.args`, `exc.errors()`, the response text or any
     other text that comes from the cause. That is the whole redaction rule (AC-6): the token can
     reach the cause's text, so nothing from the cause's text may reach the message.
   - `slug` is safe in the message: `_fetch_recipe` refuses any slug outside `[A-Za-z0-9_-]+`
     (as `KeyError`) before it sends a request.
   - A one-line docstring that states the fixed-fields rule. Exact wording is yours; the AC-2 and
     AC-3 content (status, method, path) and the fixed-fields rule are not.

Do not change `_fetch_recipe`, `_parse`, `_ingredients`, `set_meal_plan`, `list_by_tag` or
`import_url`. `_fetch_recipe` is shared with `set_meal_plan`, which must still raise raw
`httpx.HTTPStatusError` (AC-7). Add no logging, no new exception class, no constant, no module.

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

The orchestrator's local baseline before GREEN is 15 failed, 1115 passed, 7 deselected. After
GREEN it should be 0 failed, 1130 passed, 7 deselected. In your sandbox only, expect about 13
failures in `tests/test_deploy.py` (`pgrep` reports "sysmond service not found", and `ps` is
denied). They are not yours. List them under "Not run by Codex" with the reason
`sandbox: pgrep/ps`. The orchestrator re-runs them. Every other test must pass.

## Report

1. The changed file with a short summary of each piece.
2. The pytest summary line, and the result of each of the other four checks.
3. **Not run by Codex:** list every test that was skipped, deselected or failed for a sandbox
   reason, each with its reason (`opt-in live flag` for the `integration` marker,
   `sandbox: pgrep/ps` for the deploy tests, or `out of scope`). Write "none" if there are none. A
   skip or failure with no reason counts as unverified.
4. Anything in the seam map, the contracts docstrings or the frozen tests that you think is wrong.
   Report it. Do not act on it.
