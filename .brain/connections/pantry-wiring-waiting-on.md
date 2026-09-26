---
type: connection
features: [pantry, wiring]
kind: waiting-on
status: open
severity: medium
blocks: [wiring]
files: [meals/pantry.py, meals/rollup.py, meals/seed_loader.py, meals/mcp_tools.py, tests/test_pantry.py, tests/test_rollup.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[wiring]] builds on the roll-up and the ingredient→Meijer product map from [[pantry]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before pantry. Resolves automatically when pantry's presence status is `done`.

**Note for [[wiring]]'s P2 job reporting (from [[pantry]] PR 1, via [[pm]], 2026-09-26):** pantry reads **fail closed** on a hand-corrupted row. The error names the row and the fix: "pantry_item N ('x') is invalid: …; re-run the seed loader to fix it". A Saturday job that hits it must put that exception text in the job's Telegram failure report (ADR-0001's always-report step), not just the log. Otherwise Steve sees "plan failed" with no remedy.
