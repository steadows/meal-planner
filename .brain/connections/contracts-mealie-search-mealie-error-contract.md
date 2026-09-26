---
type: connection
features: [search, mealie, contracts]
kind: shared-file
status: open
severity: medium
blocks: []
files: [meals/planner.py, meals/mealie_client.py, meals/contracts.py]
discovered: 2026-09-26T15:24:07Z
resolved: null
updated: 2026-09-26T18:00:58Z
---
**Bug (found in [[mealie]]'s `mealie_slug` follow-up review, verified by [[pm]] on main):** `meals/planner.py` `_rotation_pool` catches only `KeyError` from `get_recipe`. On main, `mealie_client.get_recipe` raises `KeyError` for a 404, but lets other failures escape raw: `httpx.HTTPStatusError` for any other status (e.g. a Mealie 5xx or an ingredient-parser 500), `httpx.TransportError` for network trouble, and a validation error for an unexpected body. So **one broken rotation favourite fails the whole Saturday `propose()`** instead of being skipped.

**Recommended owned fix (not a planner-side `except Exception`, and don't import httpx into the planner):** give the `MealieClient` Protocol a documented error contract.
1. [[contracts]]: the Protocol documents that `get_recipe` raises `KeyError` (no such recipe) or one named `MealieUnavailable` error (anything else), defined in `contracts.py`; FakeMealie can raise it.
2. [[mealie]]: `mealie_client` wraps HTTP, transport and body-validation failures in `MealieUnavailable`, keeping the cause and a redacted message.
3. [[search]]: `_rotation_pool` skips on `MealieUnavailable` with a warning naming the slug, and has a test with one failing favourite among good ones.

Order: 1 → 2 and 3 (2 and 3 can run in parallel against the fake).

**[[contracts]], 2026-09-26 — step 1 is up as PR #18** (head `d35e50c`; not merged, Steve's call).
- **The contract, as reviewed:**
  - Only a 404 for the recipe itself is KeyError.
  - Everything else is `MealieUnavailable`: a 401/403 (rejected token), a parser-endpoint 404, transport errors, an unparseable body.
  - It's raised `from` the cause. The message is log-safe; the chained cause isn't vetted, so log the message, not the traceback.
  - It's scoped to `get_recipe`.
- **For [[search]] (step 3):** PR #18's security review (CWE-636 note) recommends that the "fail loudly when every call fails" check land **in the same PR** that adds the skip to `_rotation_pool`. Otherwise a revoked token mid-loop would skip every favourite quietly. Today `list_by_tag` runs first and isn't covered by this contract, so a revoked token already fails `propose()` loudly.
- **For [[mealie]] (step 2):** build the message from fixed fields, never `str(exc)` or the body. Add a MockTransport test that the token appears in neither `str` nor `repr` (agreed with mealie). This is a lane decision; if a lane prefers a different shape, discuss it on this note. Set status resolved when all three are on main.

**[[search]] (2026-09-26):** agreed on the shape. `_rotation_pool` will catch `KeyError | MealieUnavailable`, skip the slug with a warning naming it, and get a test with one `unavailable` favourite among good ones (via `FakeMealieClient(unavailable=...)`). search builds it against [[contracts]]' branch once the PR is up.
