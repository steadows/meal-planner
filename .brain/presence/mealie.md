---
type: presence
agent: mealie
feature: "Mealie client (httpx): import from URL, recipes, tags, meal plans, shopping list; compose file"
status: idle
phase: waiting on contracts
owns_branches: ["mealie"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/amap3i/meal-planner
current_branch: feat/mealie
current_ticket: none
touches: [meals/mealie_client.py, docker/mealie/, tests/test_mealie_client.py]
updated: 2026-09-25T14:17:50Z
---
Lane C. `import_url()`, `get_recipe()`, `list_by_tag()`, `set_meal_plan()` against the Mealie API (bearer token; endpoints from `/docs` on the running instance — verify, don't guess). Owns the compose file from PLAN.md Phase 1 and the tag conventions (protein, grain, veg-tray, sauce, kid-cook, lunch-build). Integration tests need Steve's running Mealie; unit tests use the fake. Waits on [[contracts-mealie-waiting-on]]. Unblocks [[mealie-wiring-waiting-on]].
