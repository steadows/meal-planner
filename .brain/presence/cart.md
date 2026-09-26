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

## ▶ Before any cart code (Steve, via [[pm]], 2026-09-25)
Two gates, in order. (1) The week-one spike with Steve present: Steve's Chrome, the Meijer login and the extension; meijer.com only, never check out. (2) `/steadows-architect` on the cart path (Meijer in Chrome vs Instacart), with the spike's results as input. See [[cart-spike-design-waiting-on]] and PLAN.md → Architecture gates.

## Runner behaviour the spike must test against (from [[contracts]], via [[pm]], 2026-09-26)
`chrome=True` runs are **never retried**, since a retry would double the cart, and get **no built-in tools**, so the Meijer session can't reach the open web. They also inherit `--safe-mode` and the allowlisted env. So run the spike with the same flags `claude_runner` uses, not a bare `claude --chrome -p`. It has to confirm the Chrome tools still work with built-ins off and in safe mode. The timeout path is bounded (process-group kill, then at most ~5s draining output), so a slot is always freed within `timeout` + ~5s. `CartItem.meijer_url` must be an https meijer.com URL. Details: `~/meal-planner-contracts/.context/seams/contracts-lane0.md` → "Pre-PR review round (2026-09-26)".
