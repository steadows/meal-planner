---
type: connection
features: [pantry, cart]
kind: waiting-on
status: open
severity: medium
blocks: [cart]
files: [meals/pantry.py, meals/rollup.py, meals/seed_loader.py, meals/mcp_tools.py, tests/test_pantry.py, tests/test_rollup.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[cart]] builds on the roll-up and the ingredient→Meijer product map from [[pantry]]. cart can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before pantry. Resolves automatically when pantry's presence status is `done`.
