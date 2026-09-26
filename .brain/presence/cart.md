---
type: presence
agent: cart
feature: "Cart fill: CartList from approved plan, Claude-in-Chrome session, CartReport parsing"
status: idle
phase: waiting on contracts + pantry (spike first)
owns_branches: ["cart"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-cart
current_branch: feat/cart
current_ticket: none
touches: [meals/cart.py, meals/prompts/cart/, tests/test_cart.py]
updated: 2026-09-25T14:17:50Z
---
Lane F — highest risk. Before building, run the spike: `claude --chrome -p` adds five items to Steve's Meijer cart; if Meijer fights automation, switch to the Instacart path and record it in research/. Cart session opens only meijer.com product URLs from the pantry map, never recipe pages. Never checks out. Needs Steve's Chrome + Meijer login (setup lane). Waits on [[cart-contracts-waiting-on]] and [[cart-pantry-waiting-on]].

## For the week-one cart spike (from [[contracts]], via [[pm]], 2026-09-25)
`claude_runner` launches `claude` with `--safe-mode` (keeps the /login session, drops Steve's CLAUDE.md, hooks and plugins) and an allowlisted child env with ANTHROPIC_API_KEY always stripped. Still unverified: does `--chrome` work under `--safe-mode`, and which env vars does Chrome control need to pass through? Both are module constants in `claude_runner.py`, so a fix is a one-line PR to [[contracts]].
