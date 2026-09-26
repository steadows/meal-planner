---
type: presence
agent: mealie
feature: "Mealie client (httpx): import from URL, recipes, tags, meal plans, shopping list; compose file"
status: active
phase: "PR #11 green, verified (steadows-verify READY @ 38fb25b), mergeable — waiting on Steve to merge; then status done (unblocks wiring) + mealie_slug follow-up after contracts #13"
owns_branches: ["mealie"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-mealie
current_branch: feat/mealie
current_ticket: none
touches: [meals/mealie_client.py, docker/mealie/, tests/test_mealie_client.py, tests/test_mealie_client_integration.py, pyproject.toml, uv.lock, docs/prompts/lane-c-mealie-adversarial-review.md]
updated: 2026-09-26T06:18:01Z
---
Lane C. `import_url()`, `get_recipe()`, `list_by_tag()`, `set_meal_plan()` against the Mealie API (bearer token; endpoints from `/docs` on the running instance — verify, don't guess). Owns the compose file from PLAN.md Phase 1 and the tag conventions (protein, grain, veg-tray, sauce, kid-cook, lunch-build). Integration tests need Steve's running Mealie; unit tests use the fake. Waits on [[contracts-mealie-waiting-on]]. Unblocks [[mealie-wiring-waiting-on]].
