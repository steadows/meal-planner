---
type: connection
features: [mealie, wiring]
kind: waiting-on
status: resolved
severity: medium
blocks: [wiring]
files: [meals/mealie_client.py, docker/mealie/, tests/test_mealie_client.py]
discovered: 2026-09-25T14:17:50Z
resolved: 2026-09-26T14:26:20Z
updated: 2026-09-26T14:26:20Z
---
[[wiring]] builds on the Mealie client from [[mealie]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before mealie. Resolves automatically when mealie's presence status is `done`.

**Interface notes from [[mealie]] for [[wiring]] (2026-09-26):**
- `set_meal_plan(week_start, slugs)` is **not safe to run concurrently for one week** (ADR-0001, confirmed: reconcile is the single publisher under its job lock; please keep it among the ADR's integration test points). A retried publish is safe: reruns replace rather than append: two overlapping calls can leave the union of their recipes. ADR-0001 already has only `reconcile` publish, under `job-reconcile.lock`. Keep it that way. It replaces only entries marked "Planned by meal-planner" and returns `week_start.isoformat()` (store as `mealie_plan_ref`). `set_meal_plan(week, [])` clears a week.
- `import_url` raises `ValueError` for a refused URL, a failed scrape, or a read timeout (Mealie may still finish that one, leaving a recipe with no slug here). It raises httpx errors for auth failures or Mealie being down. Re-importing a URL makes a copy ("Name (1)"), so prefer `option.mealie_slug` as ADR-0001 does.
- Build the client once per process: `HttpMealieClient.from_settings()` (it raises RuntimeError if `MEALIE_TOKEN` is unset).

