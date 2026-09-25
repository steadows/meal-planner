---
type: connection
features: [bot, cart, contracts, mealie, pantry, search, wiring]
kind: shared-rule
status: watch
severity: high
blocks: []
files: [meals/contracts.py, meals/db.py, meals/config.py, meals/claude_runner.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
Only [[contracts]] edits the shared contract files. Every other lane requests a change with `brain dm @contracts "<change + why>"` and a small PR against main; nobody edits these files on a lane branch. Rationale: PLAN.md → Rules so parallel agents don't collide.
