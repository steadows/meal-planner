# Mealie — GREEN repair r2: two ultrareview gaps in get_recipe → MealieUnavailable (PR #36)

**Mode: implement GREEN. Single agent: no fan-out, no subagents.** You write production code that
makes a frozen, already-failing test suite pass. You do not write or change tests.

## The goal

The post-PR ultrareview of PR #36 (head cce2aad) found two more Mealie failures that escape
`get_recipe` raw instead of as `MealieUnavailable`. New RED tests pin both. Close them with the
smallest change.

- **U1 (AC-4):** a reply of 3xx with a `Location` httpx can't parse (`https:foo`) makes
  `self._http.get/post` raise `httpx.InvalidURL`, even with redirects off (httpx builds the next
  request anyway). `InvalidURL` is neither an `httpx.HTTPError` nor a `ValueError`.
- **U2 (AC-5):** a recipe body nested deeper than the stdlib JSON decoder can follow
  (`"[" * 1100 + "0" + "]" * 1100`) makes `response.json()` in `_fetch_recipe` raise `RecursionError`.
  pydantic's JSON parser turns the same input into a `ValidationError`.

## Read first (required)

1. **The seam map: `.context/seams/mealie-unavailable.md`.** It is your authority. Read AC-4 and
   AC-5 (amended; see "added after ultrareview U1/U2"), AC-7 and AC-8, the Out of scope fence, and
   the Altitude decisions.
2. `.context/reviews/mealie-unavailable-ultrareview-r1.md`: the findings and their bins.
3. `meals/mealie_client.py`: `get_recipe`, `_fetch_recipe`, `_parse` (already reads with
   `_PARSED_RESPONSE.validate_json`), `_SLUG_RESPONSE`, `set_meal_plan`.
4. The frozen RED: `tests/test_mealie_client.py`, the new malformed-redirect test and the AC-5 case
   `recipe-nested-too-deeply` (the `not-json` case now expects a `ValueError` cause).

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
failures are the malformed-redirect test (a raw `httpx.InvalidURL`) and `recipe-nested-too-deeply`
(a raw `RecursionError`). If the count differs, or anything errors on import or collection, stop and
report. Do not implement.

## The task: two changes in `meals/mealie_client.py`, nothing else

1. **U1, `get_recipe`:** add `httpx.InvalidURL` to the `except` tuple. Our own request URLs can't be
   invalid (the slug is allowlisted before any request, and the base URL is checked when the
   `httpx.Client` is built), so an `InvalidURL` here comes from Mealie's reply. Keep the comment on
   that line short and true; it may name InvalidURL's source. Do not catch `Exception`, and do not
   catch `RecursionError` here: AC-8 needs a programming error to escape unchanged.
2. **U2, `_fetch_recipe`:** read the body with pydantic's JSON parser,
   `_Recipe.model_validate_json(response.content)`, instead of
   `_Recipe.model_validate(response.json())`. This matches `_parse` and `_SLUG_RESPONSE`, and makes
   both a non-JSON body and an over-nested body a `ValidationError`. `_fetch_recipe` is shared with
   `set_meal_plan`: its bad-body error becomes a raw `ValidationError` instead of a raw
   `JSONDecodeError` (both `ValueError`). That is accepted. AC-7 (`set_meal_plan` still raises raw
   `httpx.HTTPStatusError`) must keep passing.

Do not change `_parse`, `_paged`, `_unavailable_reason`, `list_by_tag`, `import_url` or
`from_settings`. Add no logging, no exception class, no new module.

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

The orchestrator's local result after GREEN should be 0 failed, 7 deselected. In your sandbox only,
expect about 13 failures in `tests/test_deploy.py` (`pgrep` reports "sysmond service not found", and
`ps` is denied). They are not yours. List them under "Not run by Codex" with the reason
`sandbox: pgrep/ps`. The orchestrator re-runs them. Every other test must pass.

## Report

1. The changed file with a short summary of each change.
2. The pytest summary line, and the result of each of the other four checks.
3. **Not run by Codex:** list every test that was skipped, deselected or failed for a sandbox
   reason, each with its reason (`opt-in live flag` for the `integration` marker,
   `sandbox: pgrep/ps` for the deploy tests, or `out of scope`). Write "none" if there are none. A
   skip or failure with no reason counts as unverified.
4. Anything in the seam map, the review notes or the frozen tests that you think is wrong. Report
   it. Do not act on it.
