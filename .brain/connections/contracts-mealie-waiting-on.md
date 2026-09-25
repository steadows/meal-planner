---
type: connection
features: [contracts, mealie]
kind: waiting-on
status: open
severity: medium
blocks: [mealie]
files: [meals/__init__.py, meals/config.py, meals/db.py, meals/contracts.py, meals/claude_runner.py, meals/fakes/, pyproject.toml, tests/conftest.py, tests/test_contracts.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[mealie]] builds on the shared contracts, db schema, claude_runner and fakes from [[contracts]]. mealie can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before contracts. Resolves automatically when contracts's presence status is `done`.
