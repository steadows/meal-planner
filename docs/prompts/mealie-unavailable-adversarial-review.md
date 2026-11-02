# Mealie — pre-PR adversarial sweep: get_recipe → MealieUnavailable (error-contract step 2)

You are a senior adversarial code reviewer working ALONE — do NOT spawn
sub-agents or fan out; a single-agent review is a hard requirement (the
multi-agent fleet belongs to the post-PR ultrareview gate, not this one).
Your job is to find issues that a thorough first-pass review MISSED — not
to repeat what was already found. Findings only — do not edit code. Review-only.

## Data boundary (Steve, 2026-09-25)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/` and `.context/reviews/`.
Do NOT read `.env`, anything under `data/`, or `seed/`. Do not contact a real Mealie. Do not run
`pytest -m integration`. git is not available to you here: the diff is pre-written (below).
You may run `.venv/bin/python -m pytest tests/test_mealie_client.py -q` and short
`.venv/bin/python -c` checks; write no files.

## Deference rule
The dividing line for trusting a comment, docstring, or design-doc section
is not code-vs-docs, it's authored-by-this-diff vs pre-existing. Anything
the diff under review adds or changes is a claim by the author and must be
verified against the code before you clear a finding on its authority.
Anything that predates the diff is the standard the code is measured
against. Where they disagree, the pre-existing contract wins unless the
diff explicitly and defensibly amends it. Do not generalize this to
"discount documentation" — a pre-existing design doc is frequently the
only thing that makes a finding judgeable at all.

## Not a false positive
Code that mishandles a real, reachable future condition (a version skew, a
config not yet set, a flag not yet flipped) is not a false positive just
because the triggering condition hasn't occurred yet. If the code as
written produces the wrong behavior once that condition is real, it's a
live finding now, scored on what the code does — not deferred as "future
risk."

## The done contract — what this change promised

## Done contract (no tracker; drafted here, written before code)

**Done means.** `HttpMealieClient.get_recipe` raises `KeyError` only for a recipe Mealie doesn't have, and
`contracts.MealieUnavailable` for every other HTTP, network or response-body failure, raised `from` the
cause with a message that never contains the Mealie token; the other three methods keep their current
errors; …and every AC below is proven.

**Acceptance criteria**

| AC | Given / When / Then | Proof |
|---|---|---|
| AC-1 | Given a slug Mealie answers 404 for, or a slug outside the allowlist, when `get_recipe` runs, then it raises `KeyError` (not `MealieUnavailable`) | existing `tests/test_mealie_client.py::test_get_recipe_raises_key_error_for_an_unknown_slug`, `::test_get_recipe_treats_a_slug_outside_the_allowlist_as_unknown` (stay green, unchanged) |
| AC-2 | Given the recipe GET answers 500, 401 or 403, when `get_recipe` runs, then it raises `MealieUnavailable` whose `__cause__` is the `httpx.HTTPStatusError`, and whose message names the status code and the request's method and path | new `::test_get_recipe_raises_mealie_unavailable_for_an_http_error_on_the_recipe` (parametrized 500/401/403) |
| AC-3 | Given the recipe GET succeeds but the ingredient-parser POST answers 404 or 500, when `get_recipe` runs, then it raises `MealieUnavailable` (a parser 404 is never `KeyError`), message naming the status and `POST /api/parser/ingredients` | new `::test_get_recipe_raises_mealie_unavailable_when_the_ingredient_parser_fails` (parametrized 404/500) |
| AC-4 | Given the request fails below HTTP (`httpx.ConnectError`, `httpx.ReadTimeout`, or `httpx.DecodingError` for a corrupt compressed body — a `RequestError` that is not a `TransportError`), when `get_recipe` runs, then it raises `MealieUnavailable` whose `__cause__` is that error | new `::test_get_recipe_raises_mealie_unavailable_for_a_network_failure` (parametrized; DecodingError added after the spec-watchdog pass) |
| AC-5 | Given a 200 whose body doesn't parse (not JSON; a recipe with no valid `id`; a parser answer whose item count differs from the texts sent), when `get_recipe` runs, then it raises `MealieUnavailable` whose `__cause__` is the `ValueError` (pydantic `ValidationError`, `JSONDecodeError`) | new `::test_get_recipe_raises_mealie_unavailable_for_a_body_that_does_not_parse` (parametrized, one proof per case) |
| AC-6 | Given a client built by `from_settings` with a known token, and (a) a 500 whose body echoes the token, (b) a `TransportError` whose text contains the token, (c) a recipe body whose invalid field holds the token (so the `ValidationError` text quotes it), when `get_recipe` raises, then the token appears in neither `str(err)` nor `repr(err)` | new `::test_get_recipe_keeps_the_token_out_of_mealie_unavailable` (parametrized a/b/c; each case must first show the token IS in `str(err.__cause__)` or the response body, so the test proves redaction rather than absence) |
| AC-8 | Given an error that is not HTTP, network or body (e.g. a `TypeError` from a bug), when `get_recipe` runs, then that error escapes unchanged, not as `MealieUnavailable` — so a bug crashes loudly instead of reading as "Mealie is down" and being skipped (added after the spec-watchdog pass: `except KeyError: raise` / `except Exception` passed every other test) | new `::test_get_recipe_lets_a_programming_error_through` |
| AC-7 | Given the recipe GET answers 500, when `set_meal_plan` runs, then it still raises raw `httpx.HTTPStatusError`, not `MealieUnavailable` (the wrap lives in `get_recipe`, not in the shared `_fetch_recipe`) | new `::test_set_meal_plan_still_raises_http_status_error_when_a_recipe_lookup_fails` |

**Out of scope** (reviewers DROP findings that land here)

| item | where it lives instead |
|---|---|
| Wrapping `set_meal_plan`, `list_by_tag`, `import_url` errors | not planned (contract scopes MealieUnavailable to `get_recipe`). Search flagged on 2026-11-02 that `list_by_tag` is `propose()`'s first Mealie call. When Mealie is down there, `jobs._catch_all` logs the raw httpx traceback, because `safe_exc_info` finds no MealieUnavailable in the chain. That failure is still loud, and httpx's error text carries the URL, never the Authorization header. Widening the scope is contracts' docstring call (search's Later list, other-owner) |
| `_rotation_pool` skipping on MealieUnavailable; the all-fail loud check | search, `feat/search-rotation-skip` (error-contract step 3) |
| The fake raising MealieUnavailable | done, contracts #18 |
| Logging the failure (client stays silent; caller logs `str(err)`) | search / wiring |
| Vetting the chained cause or traceback | not planned (contracts docstring: log the message, not the traceback) |
| `_MealieNotConfigured.get_recipe` raising RuntimeError | wiring's `meals/__main__.py`; `list_by_tag` raises first anyway |
| Non-finite servings (`round(inf)` → OverflowError) or 4300+-digit prep times | not planned (Mealie's pydantic serializer never emits them) |
| Retrying transient failures | not planned |
| A live-Mealie integration test for a 5xx | not planned (a live Mealie can't be made to 500 on demand) |

**Budget.** 1 PR. Review stop rule: stop when two consecutive post-PR convergence rounds find no product
defect; anything after that is recorded residual risk. Later list: empty.

### Acceptance → proof (fresh-context done check, 160 passed in tests/test_mealie_client.py)

| AC | proof | status |
|---|---|---|
| AC-1 | `tests/test_mealie_client.py::test_get_recipe_raises_key_error_for_an_unknown_slug`; `::test_get_recipe_treats_a_slug_outside_the_allowlist_as_unknown[...11 cases]` | pass |
| AC-2 | `::test_get_recipe_raises_mealie_unavailable_for_an_http_error_on_the_recipe[500,401,403]` | pass |
| AC-3 | `::test_get_recipe_raises_mealie_unavailable_when_the_ingredient_parser_fails[404,500]` | pass |
| AC-4 | `::test_get_recipe_raises_mealie_unavailable_for_a_network_failure[ConnectError,ReadTimeout,DecodingError]` | pass |
| AC-5 | `::test_get_recipe_raises_mealie_unavailable_for_a_body_that_does_not_parse[not-json,id-not-a-uuid,id-missing,parser-answers-fewer-lines-than-sent]` | pass |
| AC-6 | `::test_get_recipe_keeps_the_token_out_of_mealie_unavailable[error-body,network-error,validation-error]` | pass |
| AC-7 | `::test_set_meal_plan_still_raises_http_status_error_when_a_recipe_lookup_fails` | pass |
| AC-8 | `::test_get_recipe_lets_a_programming_error_through` | pass |

## Bin every finding

## 2. The three-bin finding rule (every review gate, every round)

Every finding from `/simplify`, `/steadows-code-review`, the Codex sweep, `/steadows-ultrareview`,
`/steadows-verify` or a seams pass gets exactly one bin:

| Bin | When | What happens |
|---|---|---|
| **FIX NOW** | It breaks an AC or the team DoD (§3), or it is a correctness/security/data-loss defect in code this story adds or changes | Fixed in this PR, or in a follow-on PR **under the same story**. The story stays open until it is fixed |
| **DROP** | A nit, style, or speculative future concern; OR pre-existing and outside this story's surface; OR it sits in the story's Out of scope list | One line in the PR summary. **No ticket** |
| **LATER** | A real defect that can't be fixed here, for exactly one reason: `post-PoC` · `other-owner` (another owner's infra or lane) · `needs-ruling` (a Steve product call) · `needs-design` (ARCHITECTURE-first) | One line on the story's **Later list**, with its reason. **No ticket from the lane** |

**Severity doesn't pick the bin; scope does.** A HIGH on this story's surface is FIX NOW. A HIGH outside it
that is pre-existing is LATER, tagged `poc-blocking`.

"It would need its own RED suite" or "the PR is getting big" is **not** a LATER reason. It means a follow-on
PR under the same story (Steve: fix it, don't enumerate it; keep PRs small).

A finding that lands in the Out of scope list is DROP: one line, no argument.

## What the first-pass review already caught (survived confidence scoring; all LOW after scoring)

1. `meals/mealie_client.py:309` (`_parse`), LOW — a 200 parser reply that is valid JSON but not a
   list (`null`, `5`) raises TypeError from iterating `response.json()`, which escapes the wrap as a
   "programming error" although Done means promises MealieUnavailable for every response-body failure.
2. `meals/mealie_client.py:204` (`get_recipe` wrap), LOW — catching every ValueError also catches
   pydantic ValidationErrors from our own `RecipeOption` / `Ingredient` construction in
   `_recipe_option`, so a local code bug would read as "Mealie couldn't serve recipe X: ValidationError"
   and be skipped (the whole-pool case still raises, but blames Mealie).
3. `meals/planner.py:213` (`_rotation_pool`, NOT in this diff; another lane's code, merged on main),
   LOW — the all-unavailable guard compares `len(unavailable)` to `len(slugs)`, which includes
   KeyError slugs; one deleted favourite plus N parser-down failures leaves an empty pool with no error.
   Live only through this diff's wrap.
4. `meals/mealie_client.py:325` (`_unavailable_reason`), LOW — `exc.request.url.path` includes any
   path prefix from the configured MEALIE_URL base URL (not a secret; Steve-only audience).
5. `meals/mealie_client.py:321-329` (`_unavailable_reason`), LOW — network-error messages carry
   only the slug and class name, no method or path, so a parser-POST failure and a recipe-GET failure
   read the same.

Discarded at scoring (score 0): `_minutes` OverflowError on a 309–4300-digit prep time (pre-existing,
fenced by the Out of scope row on non-finite numbers); KeyError passthrough from the whole body
(pre-existing, no current source besides `_fetch_recipe`); `list_by_tag` unwrapped (Out of scope).

## The diff to review

Pre-written at `.context/reviews/mealie-unavailable.diff` (`git diff origin/main...HEAD`, branch
`feat/mealie-unavailable`). Read in full after the diff: `meals/mealie_client.py`,
`meals/contracts.py` (`MealieUnavailable`, the `MealieClient` Protocol, `RecipeOption`),
`meals/planner.py` (`_rotation_pool`), `meals/jobs.py` (`safe_exc_info`, `_catch_all`), and
`tests/test_mealie_client.py` (the new tests).

## Your mandate
1. SKIP anything already covered by the findings above — no duplicates.
2. Focus on these blind-spot categories that automated reviewers commonly miss:
   - Subtle logic errors that only manifest under specific input combinations
   - Cross-file interaction bugs (change in file A breaks assumption in file B)
   - Security issues hiding in "normal" code (TOCTOU, deserialization, trust boundaries) —
     above all, any path by which the Mealie bearer token can still reach `str()` or `repr()` of
     the raised MealieUnavailable, or a log line a caller writes from it
   - Concurrency hazards (race conditions, ordering assumptions, shared state)
   - Error handling gaps where failures cascade silently
   - Backward compatibility breaks (callers of changed interfaces)
   - State machine violations (invalid transitions, missing terminal states)
3. For each finding, provide:
   - **File:** path
   - **Line:** number (if applicable)
   - **Category:** security | correctness | concurrency | compatibility | cascade-failure | state-machine
   - **Severity:** CRITICAL | HIGH | MEDIUM
   - **Bin:** FIX NOW | DROP | LATER (post-PoC | other-owner | needs-ruling | needs-design)
   - **Finding:** What the first-pass review missed and why it matters
   - **Evidence:** Why this is a real risk, not speculative
   - **Fix:** Concrete recommendation
4. If the first-pass review was thorough and you can't find material issues, say so
   explicitly. Do not invent findings to justify your existence.
5. You may also say where you disagree with a first-pass finding's severity or bin above.
