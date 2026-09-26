---
type: connection
features: [bot]
kind: waiting-on
status: resolved
severity: medium
blocks: [bot]
files: [meals/bot/]
discovered: 2026-09-26T03:14:44Z
resolved: 2026-09-26T14:21:06Z
updated: 2026-09-26T14:21:06Z
---
[[bot]] waits on the **runtime-model architecture gate** (PLAN.md → Architecture gates), run by [[wiring]] via `/steadows-architect`. The bot needs the "run in the background, reply when done" mechanism for `/find` on day one. External-only on purpose: wiring's lane status only reaches `done` when it merges last, and the blocker here is the confirmed ADR, not wiring's merge. Resolve by hand (status: resolved) once Steve confirms the ADR and it's on main. [[pm]] tracks it.

**Resolved 2026-09-26T14:21:06Z by [[wiring]]:** Steve confirmed ADR-0001 (`CONFIRM runtime-model v3`) and it is on main (PR #9, squash f9f761e). The bot builds against `meals/background.py` (`start`, `spawn_job`), which wiring ships first as P1. See docs/adr/ADR-0001-runtime-model.md → Ownership → bot.
