---
type: research
topic: Combining recipe ingredient quantities across units (shopping-list roll-up)
by: pantry
date: 2026-09-26
---
**Question:** a units library (pint) or a hand-kept table for `meals/rollup.py`?

**Answer: a hand-kept table.** That is what every comparable project does.
- **pint 0.26 dropped Python 3.11.** 0.25.3 (Mar 2026) is the last release that supports 3.11. pint also has no count units (clove, head, can, bunch, pinch). Its cup/pint are US customary only, with no "stick" of butter. Source: github.com/hgrecco/pint CHANGES and default_en.txt.
- **Mealie v3** merges shopping-list lines only when both the food and the unit match. It never converts. Maintainer: "We don't have a mechanism right now for combining different units (e.g. 1 oz garlic and 1 clove garlic)" (mealie discussions #6920; see also #5177, and bug #4936).
- **Grocy** keeps per-product `quantity_unit_conversions` (e.g. "1 dl flour = 60 g", Pack→Piece ×6). **Tandoor** keeps food-specific conversions ("1 EL olive oil = 13 g"). Both are per-ingredient tables, not a physics unit system.
- **Mealie's en-US unit seed** (`mealie/repos/seed/resources/units/locales/en-US.json`): teaspoon/tsp, tablespoon/tbsp, cup/c, pint/pt, quart/qt, gallon/gal, fluid ounce/fl oz, ml, l, mg, g, kg, ounce/oz, pound/lb, plus the count-ish units clove, head, can, bunch, pinch, sprig, dash, splash, pack and serving (each with a plural). `IngredientUnit` fields: name, plural_name, abbreviation, plural_abbreviation, use_abbreviation, fraction. These strings are what reaches `Ingredient.unit` from Mealie.
- No maintained library does ingredient-specific conversions (clove↔head) or density conversions (cup of rice↔lb). The norm is to keep incompatible units on separate lines.
