---
type: connection
features: [bot, cart, contracts, mealie, pantry, search, wiring]
kind: shared-rule
status: watch
severity: high
blocks: []
files: [meals/contracts.py, meals/db.py, meals/config.py, meals/claude_runner.py, meals/fakes/]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-26T01:20:00Z
---
Only [[contracts]] edits the shared contract files. Every other lane requests a change with `brain dm @contracts "<change + why>"` and a small PR against main; nobody edits these files on a lane branch. Rationale: PLAN.md → Rules so parallel agents don't collide.

`meals/fakes/` added to the lock 2026-09-25 (Steve, via [[pm]]): the fakes encode the contracts every lane tests against. `pyproject.toml` and `tests/conftest.py` are add-only for every lane — see PLAN.md → Rules so parallel agents don't collide.
