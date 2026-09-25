---
type: presence
agent: contracts
feature: "Shared contracts: config, db schema + migrations, pydantic contracts, claude_runner, fakes"
status: idle
phase: ready to start
owns_branches: ["contracts"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/amap3i/meal-planner
current_branch: feat/contracts
current_ticket: none
touches: [meals/__init__.py, meals/config.py, meals/db.py, meals/contracts.py, meals/claude_runner.py, meals/fakes/, pyproject.toml, tests/conftest.py, tests/test_contracts.py]
updated: 2026-09-25T14:17:50Z
---
Lane 0 — everything else builds on this. Deliver the database schema from PLAN.md (Pantry rules → Table schema), the pydantic models (WeekProposal, RecipeOption, Intent, CartList, CartReport), `claude_runner.run()` wrapping `claude -p` / `claude --chrome -p` with timeouts and JSON validation, and a fake for every contract in `meals/fakes/`. Done when fakes pass and one real `claude -p` call returns valid JSON. Merge first; then set status: done to unblock [[contracts-pantry-waiting-on]], [[contracts-mealie-waiting-on]], [[contracts-search-waiting-on]], [[bot-contracts-waiting-on]] and [[cart-contracts-waiting-on]]. Sole owner of the contracts rule: [[bot-cart-contracts-mealie-pantry-search-wiring-shared-rule]].
