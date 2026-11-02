---
type: presence
agent: mealie
feature: "Mealie client (httpx): import from URL, recipes, tags, meal plans, shopping list; compose file"
status: done
phase: "Lane A Mealie setup done on this Mac (live v3.28.0 on 127.0.0.1:9925, Keychain meal-planner-mealie-admin, token in .env, 8 tags, integration 4/4). PLAN Phase 1 ticks merged as #28 (378b705, 2026-09-28); base feat/mealie at main. Next: MealieUnavailable wrap for get_recipe on feat/mealie-unavailable, on Steve's go"
owns_branches: ["mealie"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-mealie
current_branch: feat/mealie
current_ticket: none
touches: [.brain/connections/bot-contracts-pantry-confirm-stocked.md, .brain/connections/bot-wiring-waiting-on.md, .brain/journal/2026-09-28.md, .brain/presence/contracts.md, .brain/presence/mealie.md, .brain/presence/pantry.md, .brain/presence/pm.md, .brain/presence/wiring.md, architecture-plan.html, deploy/drain.py, deploy/install.sh, deploy/local.meals.bot.plist.template, deploy/local.meals.reconcile.plist.template, deploy/local.meals.sat_nudge.plist.template, deploy/local.meals.sat_propose.plist.template, deploy/local.meals.sun_autoapprove.plist.template, deploy/README.md, deploy/uninstall.sh, docs/PLAN.md, docs/prompts/wiring-p3-deploy-adversarial-review.md, docs/prompts/wiring-p3-deploy-convergence.md, docs/prompts/wiring-p3-deploy-ultrareview.md, tests/test_deploy.py]
updated: 2026-11-02T04:19:53Z
---
Lane C. `import_url()`, `get_recipe()`, `list_by_tag()`, `set_meal_plan()` against the Mealie API (bearer token; endpoints from `/docs` on the running instance — verify, don't guess). Owns the compose file from PLAN.md Phase 1 and the tag conventions (protein, grain, veg-tray, sauce, kid-cook, lunch-build). Integration tests need Steve's running Mealie; unit tests use the fake. Waits on [[contracts-mealie-waiting-on]]. Unblocks [[mealie-wiring-waiting-on]].
