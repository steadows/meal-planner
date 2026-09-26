---
type: connection
features: [cart]
kind: waiting-on
status: open
severity: high
blocks: [cart]
files: [meals/cart.py, meals/prompts/cart/]
discovered: 2026-09-26T03:14:44Z
resolved: null
updated: 2026-09-26T03:14:44Z
---
[[cart]] waits on two things, in order, before building. (1) The **week-one cart spike**: `claude --chrome -p` adds five items to the Meijer cart. It needs Steve's Chrome profile with the Meijer login and the Claude in Chrome extension (Lane A), and it runs with Steve present. Stay on meijer.com only and never check out. (2) The **cart-path architecture gate** (PLAN.md → Architecture gates): `/steadows-architect` on Meijer-in-Chrome vs Instacart, with the spike's results as input. External blocker (Steve's Chrome). Resolve by hand once the ADR is confirmed. [[pm]] tracks it.
