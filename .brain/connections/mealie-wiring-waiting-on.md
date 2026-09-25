---
type: connection
features: [mealie, wiring]
kind: waiting-on
status: open
severity: medium
blocks: [wiring]
files: [meals/mealie_client.py, docker/mealie/, tests/test_mealie_client.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[wiring]] builds on the Mealie client from [[mealie]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before mealie. Resolves automatically when mealie's presence status is `done`.
