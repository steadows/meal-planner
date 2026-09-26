"""Quantity roll-up across recipes (PLAN.md, Phase 3): unit-aware totals, rounded up to packs.

Pure math. Imports only `contracts`, so any layer may use it.

A hand-kept unit table, not a units library: pint has no count units (clove, head, can) and has
dropped Python 3.11, and Mealie, Grocy and Tandoor all use a small per-ingredient table the same
way. Lines whose units can't be converted stay separate, which is also what Mealie does.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from meals.contracts import Ingredient

# Enough to hide float noise (0.1 + 0.2) without hiding a real excess that needs another pack.
ROUND_DIGITS = 6
_EPSILON = 1e-9

# Factor to the dimension's base unit: grams for mass, millilitres for volume (US customary).
_MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
_TSP_ML = 4.92892159375
_VOLUME = {
    "ml": 1.0,
    "l": 1000.0,
    "tsp": _TSP_ML,
    "tbsp": 3 * _TSP_ML,
    "fl oz": 6 * _TSP_ML,
    "cup": 48 * _TSP_ML,
    "pint": 96 * _TSP_ML,
    "quart": 192 * _TSP_ML,
    "gallon": 768 * _TSP_ML,
}
# Units of a bare count (no unit = one each).
_COUNT = {"dozen": 12.0}
# Count units that only convert for one ingredient, keyed by casefolded ingredient name. A garlic
# head varies (10-12 cloves); 10 is the conservative approximation.
_PER_INGREDIENT = {"garlic": {"clove": 1.0, "head": 10.0}}

# Canonical unit -> other spellings, after Mealie's en-US unit seed. "oz" is weight, as in Mealie.
_SPELLINGS: dict[str, tuple[str, ...]] = {
    "mg": ("milligram", "milligrams"),
    "g": ("gram", "grams"),
    "kg": ("kilogram", "kilograms", "kgs"),
    "oz": ("ounce", "ounces"),
    "lb": ("lbs", "pound", "pounds"),
    "ml": ("milliliter", "milliliters", "millilitre", "millilitres"),
    "l": ("liter", "liters", "litre", "litres"),
    "tsp": ("teaspoon", "teaspoons", "tsps"),
    "tbsp": ("tablespoon", "tablespoons", "tbsps", "tbs"),
    "fl oz": ("fluid ounce", "fluid ounces", "fl. oz", "fl. oz."),
    "cup": ("cups", "c"),
    "pint": ("pints", "pt"),
    "quart": ("quarts", "qt"),
    "gallon": ("gallons", "gal"),
    "clove": ("cloves",),
    "head": ("heads",),
    "can": ("cans",),
    "bunch": ("bunches",),
    "pinch": ("pinches",),
    "sprig": ("sprigs",),
    "dash": ("dashes",),
    "splash": ("splashes",),
    "pack": ("packs", "package", "packages"),
    "serving": ("servings",),
    "dozen": ("dozens", "doz"),
}
_CANONICAL = {
    spelling: canonical
    for canonical, spellings in _SPELLINGS.items()
    for spelling in (canonical, *spellings)
}


@dataclass(frozen=True)
class _Measure:
    """Lines with the same `dimension` add up; `factor` converts `unit` to the dimension's base."""

    dimension: str
    factor: float
    unit: str | None


def _key(name: str) -> str:
    return name.strip().casefold()


def _measure(name: str, unit: str | None) -> _Measure:
    if unit is None or not unit.strip():
        return _Measure("count", 1.0, None)
    folded = unit.strip().casefold()
    canonical = _CANONICAL.get(folded, folded)
    if canonical in _COUNT:
        return _Measure("count", _COUNT[canonical], canonical)
    if canonical in _MASS:
        return _Measure("mass", _MASS[canonical], canonical)
    if canonical in _VOLUME:
        return _Measure("volume", _VOLUME[canonical], canonical)
    per_ingredient = _PER_INGREDIENT.get(_key(name), {})
    if canonical in per_ingredient:
        return _Measure(f"per:{_key(name)}", per_ingredient[canonical], canonical)
    return _Measure(f"unit:{canonical}", 1.0, canonical)


def require_positive(qty: float, what: str) -> None:
    """Raise ValueError unless `qty` is a positive, finite number. `what` names it in the message."""
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError(f"{what} must be a positive, finite number, got {qty!r}")


def _join_notes(notes: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(note for note in notes if note))


def _combine_one(lines: list[Ingredient]) -> tuple[Ingredient, ...]:
    """All lines for one ingredient: one output line per dimension, in first-seen order."""
    name = lines[0].name.strip()
    quantified = [line for line in lines if line.qty is not None]
    if not quantified:
        return (Ingredient(name=name, unit=lines[0].unit, note=_join_notes(x.note for x in lines)),)
    groups: dict[str, list[Ingredient]] = {}
    for line in quantified:
        groups.setdefault(_measure(name, line.unit).dimension, []).append(line)
    unquantified_notes = [line.note for line in lines if line.qty is None]
    combined = []
    for index, group in enumerate(groups.values()):
        target = _measure(name, group[0].unit)
        base_total = sum((line.qty or 0.0) * _measure(name, line.unit).factor for line in group)
        notes = [line.note for line in group] + (unquantified_notes if index == 0 else [])
        combined.append(
            Ingredient(
                name=name,
                qty=round(base_total / target.factor, ROUND_DIGITS),
                unit=target.unit,
                note=_join_notes(notes),
            )
        )
    return tuple(combined)


def combine(ingredients: Iterable[Ingredient]) -> tuple[Ingredient, ...]:
    """Add up ingredients across recipes (½ + ½ onion = 1; 1 lb + 8 oz = 1.5 lb).

    Groups by name, ignoring case and surrounding whitespace, keeping the first-seen spelling.
    Within a name, lines that convert sum into the first-seen unit. Lines that don't stay
    separate. A line with no quantity ("to taste") is absorbed by a quantified line of the same
    name, keeping its note. Totals are rounded to ROUND_DIGITS decimals. Raises ValueError for a
    quantity that isn't positive and finite.
    """
    by_name: dict[str, list[Ingredient]] = {}
    for ingredient in ingredients:
        if ingredient.qty is not None:
            require_positive(ingredient.qty, f"quantity of {ingredient.name!r}")
        by_name.setdefault(_key(ingredient.name), []).append(ingredient)
    return tuple(line for lines in by_name.values() for line in _combine_one(lines))


def packages_needed(need: Ingredient, pack_qty: float, pack_unit: str | None) -> int | None:
    """How many packs of `pack_qty` `pack_unit` cover `need`, rounded up (1.5 lb of 3 lb packs: 1).

    None when `need` has no quantity or its unit can't be converted to the pack's. Raises
    ValueError for a quantity that isn't positive and finite.
    """
    require_positive(pack_qty, "pack_qty")
    if need.qty is None:
        return None
    require_positive(need.qty, f"quantity of {need.name!r}")
    have, pack = _measure(need.name, need.unit), _measure(need.name, pack_unit)
    if have.dimension != pack.dimension:
        return None
    packs = need.qty * have.factor / (pack_qty * pack.factor)
    return math.ceil(packs - _EPSILON)
