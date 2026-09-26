---
type: presence
agent: pantry
feature: "Pantry table logic, staples_due, learned intervals, unit-aware roll-up, seed loader, MCP tools"
status: idle
phase: waiting on contracts
owns_branches: ["pantry"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pantry
current_branch: feat/pantry
current_ticket: none
touches: [meals/pantry.py, meals/rollup.py, meals/seed_loader.py, meals/mcp_tools.py, tests/test_pantry.py, tests/test_rollup.py]
updated: 2026-09-25T14:17:50Z
---
Lane B. Item types (staple/perishable/fallback), status flips, `staples_due()`, median-gap repurchase intervals, quantity roll-up across recipes with unit normalisation (tests for 1/2+1/2, 1 lb + 8 oz, 2 cloves + 1 head), and the loader for the seed-run CSVs (a fixture CSV is fine until Steve's real one exists). Waits on [[contracts-pantry-waiting-on]]. Unblocks [[cart-pantry-waiting-on]] and [[pantry-wiring-waiting-on]].

## Open contract gap to confirm (via [[pm]], 2026-09-26)
The `Pantry` Protocol needs a "still good / have plenty" operation. Contracts proposes `confirm_stocked(name, on, plenty=False)`. See [[bot-contracts-pantry-confirm-stocked]]; confirm or amend it with [[contracts]] early.
