import pytest

from meals.contracts import Ingredient
from meals.rollup import combine, packages_needed


def _ing(name: str, qty: float | None, unit: str | None = None, note: str = "") -> Ingredient:
    return Ingredient(name=name, qty=qty, unit=unit, note=note)


# ── combine: PLAN.md, Phase 3 ────────────────────────────────────────────────


def test_half_plus_half_is_one() -> None:
    assert combine([_ing("onion", 0.5), _ing("onion", 0.5)]) == (_ing("onion", 1.0),)


def test_pound_plus_ounces_sums_in_the_first_seen_unit() -> None:
    combined = combine([_ing("ground turkey", 1, "lb"), _ing("ground turkey", 8, "oz")])
    assert combined == (_ing("ground turkey", 1.5, "lb"),)


def test_garlic_cloves_plus_a_head_sums_in_cloves() -> None:
    combined = combine([_ing("garlic", 2, "cloves"), _ing("garlic", 1, "head")])
    assert combined == (_ing("garlic", 12, "clove"),)


# ── combine: grouping and normalisation ──────────────────────────────────────


def test_names_group_case_and_whitespace_insensitively_keeping_first_display_name() -> None:
    combined = combine([_ing("Red Onion", 1), _ing(" red onion ", 2), _ing("rice", 1, "cup")])
    assert combined == (_ing("Red Onion", 3), _ing("rice", 1, "cup"))


@pytest.mark.parametrize(
    ("first", "second", "qty", "unit"),
    [
        ((1, "lbs"), (2, "pound"), 3.0, "lb"),
        ((1, "Tbsp"), (2, "tablespoons"), 3.0, "tbsp"),
        ((1, "tbsp"), (2, "tsp"), 1.666667, "tbsp"),
        ((1, "dozen"), (6, None), 1.5, "dozen"),
        ((1, "cup"), (2, "fl oz"), 1.25, "cup"),
        ((1, "kg"), (200, "g"), 1.2, "kg"),
    ],
)
def test_units_convert_within_a_dimension(
    first: tuple[float, str | None], second: tuple[float, str | None], qty: float, unit: str
) -> None:
    combined = combine([_ing("x", *first), _ing("x", *second)])
    assert combined == (_ing("x", qty, unit),)


def test_incompatible_units_stay_separate_lines() -> None:
    combined = combine([_ing("rice", 1, "cup"), _ing("rice", 2, "lb"), _ing("rice", 1, "cup")])
    assert combined == (_ing("rice", 2, "cup"), _ing("rice", 2, "lb"))


def test_a_bare_count_does_not_merge_with_a_measured_line() -> None:
    combined = combine([_ing("bell peppers", 3), _ing("bell peppers", 1, "lb")])
    assert combined == (_ing("bell peppers", 3), _ing("bell peppers", 1, "lb"))


def test_other_known_units_merge_only_with_themselves_including_plurals() -> None:
    combined = combine(
        [_ing("tomatoes", 1, "can"), _ing("tomatoes", 2, "cans"), _ing("tomatoes", 1, "lb")]
    )
    assert combined == (_ing("tomatoes", 3, "can"), _ing("tomatoes", 1, "lb"))


def test_unknown_units_merge_only_on_the_same_string() -> None:
    combined = combine(
        [_ing("bread", 2, "slice"), _ing("bread", 1, "Slice"), _ing("bread", 1, "loaf")]
    )
    assert combined == (_ing("bread", 3, "slice"), _ing("bread", 1, "loaf"))


def test_the_head_to_clove_conversion_is_garlic_only() -> None:
    combined = combine([_ing("lettuce", 1, "head"), _ing("lettuce", 2, "cloves")])
    assert combined == (_ing("lettuce", 1, "head"), _ing("lettuce", 2, "clove"))


def test_an_unquantified_line_is_absorbed_by_a_quantified_one() -> None:
    combined = combine([_ing("salt", None, note="to taste"), _ing("salt", 1, "tsp")])
    assert combined == (_ing("salt", 1, "tsp", note="to taste"),)


def test_unquantified_lines_alone_collapse_to_one() -> None:
    assert combine([_ing("salt", None), _ing("salt", None)]) == (_ing("salt", None),)


def test_notes_are_joined_without_duplicates() -> None:
    combined = combine(
        [
            _ing("feta", 4, "oz", "crumbled"),
            _ing("feta", 4, "oz", "crumbled"),
            _ing("feta", 2, "oz", "or cotija"),
        ]
    )
    assert combined == (_ing("feta", 10, "oz", "crumbled; or cotija"),)


def test_float_noise_is_rounded_away() -> None:
    combined = combine([_ing("milk", 0.1, "cup"), _ing("milk", 0.2, "cup")])
    assert combined == (_ing("milk", 0.3, "cup"),)


def test_real_precision_survives_the_rounding() -> None:
    combined = combine([_ing("flour", 1, "kg"), _ing("flour", 0.4, "g")])
    assert combined == (_ing("flour", 1.0004, "kg"),)
    assert packages_needed(combined[0], 1, "kg") == 2


def test_empty_input_gives_empty_output() -> None:
    assert combine([]) == ()


@pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf")])
def test_combine_rejects_a_quantity_that_is_not_positive_and_finite(qty: float) -> None:
    with pytest.raises(ValueError):
        combine([_ing("rice", 1, "cup"), _ing("rice", qty, "cup")])


# ── packages_needed: PLAN.md, Risks ("rounded up to buyable units") ──────────


@pytest.mark.parametrize(
    ("need", "pack_qty", "pack_unit", "expected"),
    [
        (_ing("chicken thighs", 1.5, "lb"), 3, "lb", 1),
        (_ing("chicken thighs", 4, "lb"), 3, "lb", 2),
        (_ing("ground turkey", 24, "oz"), 1, "lb", 2),
        (_ing("ground turkey", 16, "oz"), 1, "lb", 1),
        (_ing("garlic", 12, "clove"), 1, "head", 2),
        (_ing("eggs", 18), 12, None, 2),
        (_ing("eggs", 18), 1, "dozen", 2),
        (_ing("olive oil", 0.1 + 0.2, "cup"), 0.3, "cup", 1),
    ],
)
def test_packages_needed_rounds_up_in_the_packs_unit(
    need: Ingredient, pack_qty: float, pack_unit: str | None, expected: int
) -> None:
    assert packages_needed(need, pack_qty, pack_unit) == expected


@pytest.mark.parametrize(
    ("need", "pack_unit"),
    [(_ing("rice", 2, "cup"), "lb"), (_ing("salt", None), "oz"), (_ing("eggs", 6), "lb")],
)
def test_packages_needed_is_none_when_not_convertible(need: Ingredient, pack_unit: str) -> None:
    assert packages_needed(need, 1, pack_unit) is None


@pytest.mark.parametrize(("need_qty", "pack_qty"), [(1, 0), (1, float("nan")), (-1, 1), (0, 1)])
def test_packages_needed_rejects_quantities_that_are_not_positive_and_finite(
    need_qty: float, pack_qty: float
) -> None:
    with pytest.raises(ValueError):
        packages_needed(_ing("rice", need_qty, "lb"), pack_qty, "lb")
