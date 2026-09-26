---
type: connection
features: [pantry, bot, contracts, search, cart]
kind: shared-file
status: watch
severity: medium
blocks: []
files: [meals/contracts.py]
discovered: 2026-09-26T03:24:11Z
resolved: null
updated: 2026-09-26T05:05:00Z
---
**Open contract gap (from the [[contracts]] Codex sweep, routed by [[pm]]).** The `Pantry` Protocol has no "still good, ask later" operation. PLAN.md (Pantry rules) distinguishes two replies to a pantry question: "still good" pushes the next ask back a week and lengthens the learned estimate, and "we have plenty" pushes it back one interval. `flip_status(name, 'have')` can't express either, so [[bot]] can't tell [[pantry]] which one Steve meant.

**Proposal from [[contracts]], for pantry and bot to confirm or amend:** `Pantry.confirm_stocked(name, on: date, plenty: bool = False) -> PantryItem | None`. The postponement maths stays inside `pantry.py`.

**Widened to the whole `Pantry` Protocol ask ([[pantry]], 2026-09-26, seams pass).** Draft additions, one consolidated contracts PR:
- `confirm_stocked(name, on, plenty=False) -> PantryItem | None` ([[bot]]: "still good" / "have plenty")
- `log_purchase(name, on, qty=None, price_cents=None) -> PantryItem | None` (the "ordered" path: [[bot]] / wiring's `plan_state`)
- `get_item(name) -> PantryItem | None`, matching name or alias case-insensitively ([[cart]]: product map, `have` check)
- `list_items() -> tuple[PantryItem, ...]` ([[search]]'s planner filters `category == "fallback"` itself. Confirmed by search 2026-09-26: it needs nothing else beyond `staples_due`. Its pantry_questions are the 2 most overdue staples plus 1 fallback check, the one with the oldest `last_purchased`.)

**Next step:** whichever of [[pantry]] or [[bot]] builds first confirms the shape, then sends [[contracts]] a small PR against `contracts.py` (plus the fake), or messages the shape and contracts adds it. Only contracts edits `contracts.py` and `fakes/`. Set status resolved once it's on main.

**Sent to [[contracts]] 2026-09-26 (consolidated ask, via SendMessage).** (1) A migration adding a nullable `pantry_item.next_ask_on DATE`, plus `PantryItem.next_ask_on`. A Codex thought-partner pass showed that stretching `typical_interval_days` to postpone asks compounds under at-least-once replay (70 → 147 → 233 with no time passing). An explicit date set from `on` is idempotent. (2) The four Protocol methods above, with `confirm_stocked`'s shape confirmed unchanged. (3) `FakePantry` parity with `SqlitePantry`: `casefold`, name-before-alias, and ties broken by name. (4) Low priority: `(seed_loader)` in the entry-point layer. **Pantry PR 1 does not depend on any of this.** It ships rollup, reads, `staples_due`, `flip_status`, `log_purchase`, `get_item`, `list_items` and the seed loader on the concrete class. Pantry PR 2 (`confirm_stocked` + `next_ask_on`) waits on the migration. If contracts doesn't pick this up: blocked, waiting on Steve to restart contracts.

**Migration sequencing ([[pm]], 2026-09-26 ~01:00): two lanes are asking [[contracts]] for schema changes.** `db.MIGRATIONS` is append-only, so contracts assigns numbers in the order the PRs merge. Don't pre-claim "migration 2" in either lane's code.
- [[pantry]]: `pantry_item.next_ask_on` (the explicit "ask again on" date for "still good / have plenty"). **Not gated**; it can go first.
- [[wiring]]: the `job_run` table. **Gated on Steve confirming ADR-0001.**
If both land in one contracts PR, fine. Otherwise whichever merges first is 2.
