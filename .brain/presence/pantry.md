---
type: presence
agent: pantry
feature: "Pantry table logic, staples_due, learned intervals, unit-aware roll-up, seed loader, MCP tools"
status: idle
phase: "Phase 3 / Lane B — PR 2b (joint F2) PAUSED at wrap, 2026-09-28 11:45 EDT: branch feat/pantry-f2-ask-date pushed @ de616df (RED+GREEN+/simplify+/code-review done; Codex sweep and PR not yet). Waiting on Steve's product call: floor semantics (\"still good\" at least a week, always; pantry + contracts recommend) vs ship as built. See [[bot-contracts-pantry-confirm-stocked]]"
owns_branches: ["pantry"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pantry
current_branch: feat/pantry-f2-ask-date
current_ticket: none
touches: [.brain/presence/pantry.md, meals/pantry.py, meals/mcp_tools.py, tests/test_pantry_confirm.py, tests/test_pantry_parity.py]
updated: 2026-09-28T15:45:04Z
---
Lane B. Item types (staple/perishable/fallback), status flips, `staples_due()`, median-gap repurchase intervals, quantity roll-up across recipes with unit normalisation (tests for 1/2+1/2, 1 lb + 8 oz, 2 cloves + 1 head), and the loader for the seed-run CSVs (a fixture CSV is fine until Steve's real one exists). Waits on [[contracts-pantry-waiting-on]]. Unblocks [[cart-pantry-waiting-on]] and [[pantry-wiring-waiting-on]].

## Open contract gap to confirm (via [[pm]], 2026-09-26)
The `Pantry` Protocol needs a "still good / have plenty" operation. Contracts proposes `confirm_stocked(name, on, plenty=False)`. See [[bot-contracts-pantry-confirm-stocked]]; confirm or amend it with [[contracts]] early.

## Product-map URLs must be percent-encoded (from [[contracts]] PR #6, accepted LOW, 2026-09-26)
`CartItem.meijer_url` validation is a strict ASCII allowlist: https, a meijer.com host, and no backslash, whitespace, userinfo or raw non-ASCII. A URL with raw non-ASCII in the path is rejected, while the percent-encoded form passes. Store product-map and seed URLs percent-encoded.
