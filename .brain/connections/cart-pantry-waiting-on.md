---
type: connection
features: [pantry, cart]
kind: waiting-on
status: open
severity: medium
blocks: [cart]
files: [meals/pantry.py, meals/rollup.py, meals/seed_loader.py, meals/mcp_tools.py, tests/test_pantry.py, tests/test_rollup.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-25T14:17:50Z
---
[[cart]] builds on the roll-up and the ingredient→Meijer product map from [[pantry]]. cart can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before pantry. Resolves automatically when pantry's presence status is `done`.

**Notes for [[cart]] from [[pantry]] (2026-09-26, seams pass):**
- **What pantry gives you:** `rollup.combine(ingredients)` sums lines by name, and within a name by unit dimension. Map names to canonical pantry names with `Pantry.get_item` first; rollup never touches the pantry. `rollup.packages_needed(need, pack_qty, pack_unit)` rounds up to whole packs, or returns `None` when the units don't convert. `Pantry.get_item` / `list_items` give you `meijer_url`, `preferred_product_name`, `substitute_ok`, `status` and `category`. These are on the Protocol via the contracts PR (see [[bot-contracts-pantry-confirm-stocked]]).
- **`default_qty` / `default_unit` is the usual purchase amount** (e.g. 2 dozen eggs). It is not necessarily the pack size, and the schema has no pack-size column. Decide how you read it for `packages_needed`, and ask pantry if you need a pack-size field.
- **Saving a newly approved Meijer product** (PLAN edge case: "whatever Steve keeps gets saved to the map") has to be a pantry-owned write, because pantry owns `pantry_item` rows. When you need it, ask for `save_product(name, meijer_url, preferred_product_name, meijer_product_id)` and pantry adds it. It's not built yet, since nothing calls it.
- Stored URLs are percent-encoded and validated by `MeijerUrl` on every read, so a bad row fails closed before it reaches a `CartItem`.
