---
type: connection
features: [pantry, bot, contracts]
kind: shared-file
status: watch
severity: medium
blocks: []
files: [meals/contracts.py]
discovered: 2026-09-26T03:24:11Z
resolved: null
updated: 2026-09-26T03:24:11Z
---
**Open contract gap (from the [[contracts]] Codex sweep, routed by [[pm]]).** The `Pantry` Protocol has no "still good, ask later" operation. PLAN.md (Pantry rules) distinguishes two replies to a pantry question: "still good" pushes the next ask back a week and lengthens the learned estimate, and "we have plenty" pushes it back one interval. `flip_status(name, 'have')` can't express either, so [[bot]] can't tell [[pantry]] which one Steve meant.

**Proposal from [[contracts]], for pantry and bot to confirm or amend:** `Pantry.confirm_stocked(name, on: date, plenty: bool = False) -> PantryItem | None`. The postponement maths stays inside `pantry.py`.

**Next step:** whichever of [[pantry]] or [[bot]] builds first confirms the shape, then sends [[contracts]] a small PR against `contracts.py` (plus the fake), or messages the shape and contracts adds it. Only contracts edits `contracts.py` and `fakes/`. Set status resolved once it's on main.
