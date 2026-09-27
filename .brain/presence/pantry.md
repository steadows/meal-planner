---
type: presence
agent: pantry
feature: "Pantry table logic, staples_due, learned intervals, unit-aware roll-up, seed loader, MCP tools"
status: idle
phase: "Phase 3 / Lane B — PR 3 #25 MERGED (511fdad, 2026-09-26 20:18 EDT; main CI green on c0e41f8 with #26). Base feat/pantry ff-synced to main. Next: PR 2b (joint F2 + Steve's plenty floor max(interval,7) + delete _PantryTools, #24 is on main) stacked on contracts feat/contracts-f2-ask-date when contracts pings"
owns_branches: ["pantry"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pantry
current_branch: feat/pantry
current_ticket: none
touches: [.brain/connections/bot-contracts-pantry-confirm-stocked.md, .brain/journal/2026-09-26.md, .brain/presence/contracts.md, .brain/presence/pantry.md, .brain/presence/wiring.md, architecture-plan.html, docs/PLAN.md, docs/prompts/pantry-pr3-adversarial-review.md, docs/prompts/pantry-pr3-ultrareview.md, docs/prompts/wiring-p2-jobs-adversarial-review.md, docs/prompts/wiring-p2-jobs-convergence.md, docs/prompts/wiring-p2-jobs-ultrareview.md, meals/__main__.py, meals/background.py, meals/contracts.py, meals/fakes/pantry.py, meals/jobs.py, meals/mcp_tools.py, meals/plan_state.py, pyproject.toml, tests/test_contracts.py, tests/test_jobs_cart.py, tests/test_jobs_send.py, tests/test_jobs_weekend.py, tests/test_main.py, tests/test_mcp_tools.py, tests/test_plan_state.py, uv.lock]
updated: 2026-09-27T00:46:49Z
---
Lane B. Item types (staple/perishable/fallback), status flips, `staples_due()`, median-gap repurchase intervals, quantity roll-up across recipes with unit normalisation (tests for 1/2+1/2, 1 lb + 8 oz, 2 cloves + 1 head), and the loader for the seed-run CSVs (a fixture CSV is fine until Steve's real one exists). Waits on [[contracts-pantry-waiting-on]]. Unblocks [[cart-pantry-waiting-on]] and [[pantry-wiring-waiting-on]].

## Open contract gap to confirm (via [[pm]], 2026-09-26)
The `Pantry` Protocol needs a "still good / have plenty" operation. Contracts proposes `confirm_stocked(name, on, plenty=False)`. See [[bot-contracts-pantry-confirm-stocked]]; confirm or amend it with [[contracts]] early.

## Product-map URLs must be percent-encoded (from [[contracts]] PR #6, accepted LOW, 2026-09-26)
`CartItem.meijer_url` validation is a strict ASCII allowlist: https, a meijer.com host, and no backslash, whitespace, userinfo or raw non-ASCII. A URL with raw non-ASCII in the path is rejected, while the percent-encoded form passes. Store product-map and seed URLs percent-encoded.
