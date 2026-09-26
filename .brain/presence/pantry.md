---
type: presence
agent: pantry
feature: "Pantry table logic, staples_due, learned intervals, unit-aware roll-up, seed loader, MCP tools"
status: done
phase: "PR #12 merged to main (9ebad53, 2026-09-26) — pantry PR 1: rollup, SqlitePantry (reads, flip, log_purchase, load_seed), seed_loader. Next when resumed: PR 2 confirm_stocked + next_ask_on (after contracts #13), PR 3 mcp_tools"
owns_branches: ["pantry"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pantry
current_branch: feat/pantry
current_ticket: none
touches: [.brain/journal/2026-09-26.md, .brain/presence/pantry.md, docs/prompts/pantry-pr1-adversarial-review.md, docs/prompts/pantry-pr1-ultrareview.md, meals/pantry.py, meals/rollup.py, meals/seed_loader.py, tests/conftest.py, tests/fixtures/seed_pantry.csv, tests/test_pantry_writes.py, tests/test_pantry.py, tests/test_rollup.py, tests/test_seed_loader.py]
updated: 2026-09-26T14:53:13Z
---
Lane B. Item types (staple/perishable/fallback), status flips, `staples_due()`, median-gap repurchase intervals, quantity roll-up across recipes with unit normalisation (tests for 1/2+1/2, 1 lb + 8 oz, 2 cloves + 1 head), and the loader for the seed-run CSVs (a fixture CSV is fine until Steve's real one exists). Waits on [[contracts-pantry-waiting-on]]. Unblocks [[cart-pantry-waiting-on]] and [[pantry-wiring-waiting-on]].

## Open contract gap to confirm (via [[pm]], 2026-09-26)
The `Pantry` Protocol needs a "still good / have plenty" operation. Contracts proposes `confirm_stocked(name, on, plenty=False)`. See [[bot-contracts-pantry-confirm-stocked]]; confirm or amend it with [[contracts]] early.

## Product-map URLs must be percent-encoded (from [[contracts]] PR #6, accepted LOW, 2026-09-26)
`CartItem.meijer_url` validation is a strict ASCII allowlist: https, a meijer.com host, and no backslash, whitespace, userinfo or raw non-ASCII. A URL with raw non-ASCII in the path is rejected, while the percent-encoded form passes. Store product-map and seed URLs percent-encoded.
