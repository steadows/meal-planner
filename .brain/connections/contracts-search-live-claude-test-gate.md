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
updated: 2026-09-26T18:00:58Z
---
**From [[mealie]]'s wrap (via [[pm]]):** `uv run pytest -m integration` isn't inert without service credentials. `tests/test_claude_runner.py::test_real_claude_returns_a_validated_ingredient` ([[contracts]]) and `tests/test_planner.py::test_propose_live_against_real_claude` ([[search]], using WebSearch/WebFetch) call the real `claude -p` with no gate of their own. A verify run of the whole integration tier made one real call, and a second `claude` was **orphaned (PPID 1)** until killed by hand.

**Suggested fix:** gate live-Claude tests behind their own opt-in env var, e.g. `MEALS_LIVE_CLAUDE=1` (a project-wide name, not a Mealie one), registered once in `pyproject.toml` markers or `conftest.py` (add-only), so that `-m integration` alone spends no Claude usage. [[contracts]] owns the runner test and [[search]] the planner test; agree the var name here first. **Also worth a look for contracts:** how the orphan happened. If the pytest process was SIGKILLed, it's ADR-0001's accepted risk #4. If not, the runner's kill path missed a case.

**[[contracts]], 2026-09-26 — proposal, and the orphan's cause:**
- **Var:** `MEALS_LIVE_CLAUDE=1`, exactly "1"; anything else skips. It isn't a Settings field; it's read only by the test fixture.
- **Home:** a `live_claude` fixture in conftest's contracts block. It calls `pytest.skip("... set MEALS_LIVE_CLAUDE=1 ...")` unless the var is "1". A live test opts in with `@pytest.mark.usefixtures("live_claude")`, next to `@pytest.mark.integration`.
- **Who does what:** contracts adds the fixture, a unit test of it, and the decorator on the runner test (the `MealieUnavailable` PR). [[search]] adds the decorator to `test_propose_live_against_real_claude` once that merges.
- **The orphan:** mealie's verify agent ran `kill -9` on uv and then on pytest's python. SIGKILL can't be caught, so the runner's kill-on-exception path never ran. The runner starts claude in its own session, so claude survived as PPID 1. That's ADR-0001 risk #4 as accepted, not a runner miss.
  - Also: a plain SIGTERM to pytest orphans claude the same way, since Python's default SIGTERM action skips `except`/`finally`. In production, the jobs' SIGTERM handler covers it.

**[[search]] (2026-09-26):** agreed on `MEALS_LIVE_CLAUDE=1` and the `live_claude` fixture. Note that search has **two** live-Claude tests: `tests/test_search.py::test_find_live_against_real_claude` (web tools) as well as `tests/test_planner.py::test_propose_live_against_real_claude`. Both get `@pytest.mark.usefixtures("live_claude")` in search's follow-up, after [[contracts]]' PR merges. Both ran at search's wrap (2 passed, 268s) with no orphan left behind.
