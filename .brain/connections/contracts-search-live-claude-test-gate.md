---
type: connection
features: [contracts, search]
kind: shared-file
status: open
severity: medium
blocks: []
files: [tests/test_claude_runner.py, tests/test_planner.py, pyproject.toml]
discovered: 2026-09-26T17:51:21Z
resolved: null
updated: 2026-09-26T17:51:21Z
---
**From [[mealie]]'s wrap (via [[pm]]):** `uv run pytest -m integration` isn't inert without service credentials. `tests/test_claude_runner.py::test_real_claude_returns_a_validated_ingredient` ([[contracts]]) and `tests/test_planner.py::test_propose_live_against_real_claude` ([[search]], using WebSearch/WebFetch) call the real `claude -p` with no gate of their own. A verify run of the whole integration tier made one real call, and a second `claude` was **orphaned (PPID 1)** until killed by hand.

**Suggested fix:** gate live-Claude tests behind their own opt-in env var, e.g. `MEALS_LIVE_CLAUDE=1` (a project-wide name, not a Mealie one), registered once in `pyproject.toml` markers or `conftest.py` (add-only), so that `-m integration` alone spends no Claude usage. [[contracts]] owns the runner test and [[search]] the planner test; agree the var name here first. **Also worth a look for contracts:** how the orphan happened. If the pytest process was SIGKILLed, it's ADR-0001's accepted risk #4. If not, the runner's kill path missed a case.
