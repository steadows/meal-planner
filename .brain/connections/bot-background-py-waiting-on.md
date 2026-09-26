---
type: connection
features: [bot]
kind: waiting-on
status: open
severity: medium
blocks: [bot]
files: [meals/bot/]
discovered: 2026-09-26T14:23:03Z
resolved: null
updated: 2026-09-26T14:23:03Z
---
[[bot]] builds against [[wiring]]'s `meals/background.py` (`background.start` / `spawn_job`), which wiring ships first as ADR-0001 phase **P1**. External-only on purpose: wiring's lane status reaches `done` only when it merges last, but the blocker here is the P1 PR. Resolve by hand (status: resolved) once the P1 PR is on main. Replaces the now-resolved [[bot-runtime-design-waiting-on]]. [[pm]] tracks it.
