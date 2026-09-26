---
type: connection
features: [bot]
kind: waiting-on
status: open
severity: medium
blocks: [bot]
files: [meals/bot/]
discovered: 2026-09-26T03:14:44Z
resolved: null
updated: 2026-09-26T03:14:44Z
---
[[bot]] waits on the **runtime-model architecture gate** (PLAN.md → Architecture gates), run by [[wiring]] via `/steadows-architect`. The bot needs the "run in the background, reply when done" mechanism for `/find` on day one. External-only on purpose: wiring's lane status only reaches `done` when it merges last, and the blocker here is the confirmed ADR, not wiring's merge. Resolve by hand (status: resolved) once Steve confirms the ADR and it's on main. [[pm]] tracks it.
