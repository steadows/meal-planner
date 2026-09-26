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
updated: 2026-09-26T14:58:04Z
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

**Decided by [[contracts]] 2026-09-26, pm accepted: purchase dedupe key.** Migration 2 adds `UNIQUE (item_id, purchased_on)` on `purchase_log`, and `Pantry.log_purchase` is "one purchase per item per day: a repeat for the same day is a replay and writes nothing". A legitimate second same-day purchase from another source would lose its qty/price. That's accepted for now, because `log_purchase` has no `source` parameter and its only producer is the "ordered" path; no Intent kind logs a manual purchase. **Revisit trigger for [[bot]]:** if you add a manual-purchase intent ("grabbed eggs at the gas station"), ask [[contracts]] for a `source` parameter on `log_purchase` plus a migration re-keying the index to `(item_id, purchased_on, source)`.

**Amended 2026-09-26 ([[pantry]] → [[contracts]], after [[pm]] pushed back on stacking):** keep `confirm_stocked` OFF the Protocol in the contracts PR. Everything else ships as asked. Pantry PR 1 (no `confirm_stocked`) then stays green in either merge order. Order from there: pantry PR 2 implements `confirm_stocked` on top of the migration, then a one-line contracts PR adds it to the Protocol. The spec contracts asked for (staples_due ask date and sort key, log_purchase, confirm_stocked, list_items order) is in the SendMessage thread and in pantry's seam map, Revision 2.


**Landed 2026-09-26 ([[contracts]] PR #13, merged as 1cc239b):**
- Migration 2 (`pantry_item.next_ask_on`, and a UNIQUE `purchase_log(item_id, purchased_on)`).
- `PantryItem.next_ask_on`.
- Pantry Protocol: the ask-date rule, names unique under casefold, `log_purchase`/`get_item`/`list_items`.
- FakePantry with `confirm_stocked`: fake-only, never pulls an ask earlier.

**Still open:** the one-line contracts PR adding `confirm_stocked(name, on, plenty=False)` to the Protocol, once [[pantry]] PR 2 implements it on SqlitePantry. Close this note then.
