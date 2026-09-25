---
type: connection
features: [search, wiring]
kind: waiting-on
status: open
severity: medium
blocks: [wiring]
files: [meals/search.py, meals/planner.py, meals/prefs.yaml, meals/prompts/search/, meals/prompts/planner/, tests/test_search.py, tests/test_planner.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[wiring]] builds on search and the planner from [[search]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before search. Resolves automatically when search's presence status is `done`.
