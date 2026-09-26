import subprocess
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from meals.contracts import (
    CartItem,
    CartList,
    CartReport,
    ClaudeRunnerError,
    Components,
    Intent,
    MealieClient,
    Pantry,
    PantryItem,
    RecipeOption,
    WeekProposal,
)
from meals.fakes import FakeClaudeRunner, FakeMealieClient, FakePantry

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── layering ─────────────────────────────────────────────────────────────────


def test_import_layering_contract_holds() -> None:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run([lint_imports], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# ── models ───────────────────────────────────────────────────────────────────


def _proposal(**overrides: object) -> WeekProposal:
    fields: dict[str, object] = {
        "week_start": date(2026, 9, 27),
        "custody": "wed+fri_sat",
        "recipe_options": (),
        "components": Components(proteins=("shredded chicken",)),
        "lunch_builds": ("chicken sweet-potato bowl",),
        "kid_nights": ("Wed cook-with-Miles (ravioli)",),
        "pantry_questions": ("olive oil",),
    }
    return WeekProposal.model_validate(fields | overrides)


def test_week_proposal_defaults_to_mix_mode() -> None:
    assert _proposal().mode == "mix"


def test_week_proposal_caps_pantry_questions_at_three() -> None:
    _proposal(pantry_questions=("olive oil", "rice", "butter"))
    with pytest.raises(ValidationError):
        _proposal(pantry_questions=("olive oil", "rice", "butter", "tahini"))


@pytest.mark.parametrize(
    ("model", "field", "bad"),
    [
        (WeekProposal, "mode", "keto"),
        (WeekProposal, "custody", "every_day"),
        (Intent, "kind", "delete_everything"),
    ],
)
def test_closed_vocabularies_reject_unknown_values(
    model: type[BaseModel], field: str, bad: str
) -> None:
    base: dict[str, object] = (
        _proposal().model_dump() if model is WeekProposal else {"kind": "pick", "args": {}}
    )
    with pytest.raises(ValidationError):
        model.model_validate(base | {field: bad})


def test_models_are_frozen(sample_recipe: RecipeOption) -> None:
    with pytest.raises(ValidationError):
        sample_recipe.name = "something else"  # type: ignore[misc]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"kind": "pick", "args": {}, "confidence": 0.9})


def test_cart_report_subtotal_is_non_negative_cents() -> None:
    assert (
        CartReport(added=("eggs",), substituted=(), missing=(), subtotal_cents=5800).subtotal_cents
        == 5800
    )
    with pytest.raises(ValidationError):
        CartReport(added=(), substituted=(), missing=(), subtotal_cents=-1)


# The cart lane opens meijer_url in the logged-in Chrome session: https on www.meijer.com only.
MEIJER_URL_OWNERS: dict[type[BaseModel], dict[str, object]] = {
    CartItem: {"name": "eggs", "qty": 1},
    PantryItem: {"id": 1, "name": "eggs", "category": "perishable"},
}


@pytest.mark.parametrize("model", list(MEIJER_URL_OWNERS), ids=lambda m: m.__name__)
@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://www.meijer.com/shopping/p/eggs/123.html",
        "HTTPS://WWW.MEIJER.COM/p/1.html",
        "https://meijer.com:443/x",
    ],
)
def test_meijer_url_accepts_meijer_pages(model: type[BaseModel], url: str | None) -> None:
    item = model.model_validate(MEIJER_URL_OWNERS[model] | {"meijer_url": url})

    stored = item.model_dump()["meijer_url"]
    assert (None if stored is None else str(stored)) == url


@pytest.mark.parametrize("model", list(MEIJER_URL_OWNERS), ids=lambda m: m.__name__)
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        "http://www.meijer.com/x",
        "https://meijer.com.evil.example/x",
        # Python's urlsplit and a browser (WHATWG) disagree on these; reject anything ambiguous.
        pytest.param("https://evil.example\\@meijer.com/", id="backslash-before-userinfo"),
        pytest.param("https://user@www.meijer.com/x", id="userinfo"),
        pytest.param("https://www.meijer.com/x y", id="whitespace"),
        pytest.param("https://www.meijer.com\t@evil.example/", id="tab"),
        pytest.param("https://www.meijer.com\\evil", id="backslash"),
        pytest.param("https://me\u0131jer.com/", id="dotless-i"),
        pytest.param("https://me\u0130jer.com/", id="dotted-capital-i"),
        pytest.param("https://www.meijer.com/x\u00a0y", id="no-break-space"),
        pytest.param("https://www.meijer.com/x\x00", id="c0-control"),
    ],
)
def test_meijer_url_rejects_other_sites(model: type[BaseModel], url: str) -> None:
    with pytest.raises(ValidationError, match="meijer_url"):
        model.model_validate(MEIJER_URL_OWNERS[model] | {"meijer_url": url})


def test_cart_item_qty_must_be_positive() -> None:
    assert CartItem(name="eggs", qty=0.5).qty == 0.5
    with pytest.raises(ValidationError, match="qty"):
        CartItem(name="eggs", qty=0)


def test_pantry_item_interval_is_positive_when_set() -> None:
    """A zero-day interval would divide by zero in every due-date calculation."""
    assert PantryItem(id=1, name="rice", category="staple").typical_interval_days is None
    item = PantryItem(id=1, name="rice", category="staple", typical_interval_days=1)
    assert item.typical_interval_days == 1
    for days in (0, -7):
        with pytest.raises(ValidationError, match="typical_interval_days"):
            PantryItem(id=1, name="rice", category="staple", typical_interval_days=days)


def test_claude_runner_error_keeps_raw_output() -> None:
    err = ClaudeRunnerError("invalid JSON", raw_output="not json")
    assert err.raw_output == "not json"
    assert "invalid JSON" in str(err)


# ── fakes satisfy the Protocols ──────────────────────────────────────────────


def test_fakes_implement_protocols(fake_mealie: FakeMealieClient, fake_pantry: FakePantry) -> None:
    assert isinstance(fake_mealie, MealieClient)
    assert isinstance(fake_pantry, Pantry)


# ── FakeClaudeRunner ─────────────────────────────────────────────────────────


def test_fake_claude_returns_queued_responses_in_order_and_records_calls() -> None:
    fake = FakeClaudeRunner([{"a": 1}, {"a": 2}])
    assert fake.run("first") == {"a": 1}
    assert fake.run("second", chrome=True, timeout=30) == {"a": 2}
    assert [(c.prompt, c.chrome, c.timeout) for c in fake.calls] == [
        ("first", False, 600),
        ("second", True, 30),
    ]


def test_fake_claude_validates_against_schema(sample_recipe: RecipeOption) -> None:
    fake = FakeClaudeRunner([sample_recipe.model_dump(), {"name": "missing everything else"}])
    assert fake.run("find", schema=RecipeOption) == sample_recipe
    with pytest.raises(ClaudeRunnerError):
        fake.run("find", schema=RecipeOption)


def test_fake_claude_raises_queued_errors() -> None:
    fake = FakeClaudeRunner([ClaudeRunnerError("timed out", raw_output="")])
    with pytest.raises(ClaudeRunnerError, match="timed out"):
        fake.run("slow")


def test_fake_claude_fails_loudly_when_nothing_is_queued() -> None:
    with pytest.raises(AssertionError, match="no response queued"):
        FakeClaudeRunner().run("unexpected call")


# ── FakeMealieClient ─────────────────────────────────────────────────────────


def test_fake_mealie_lists_and_gets_by_slug(
    fake_mealie: FakeMealieClient, sample_recipe: RecipeOption
) -> None:
    assert fake_mealie.list_by_tag("rotation") == ("sheet-pan-chicken-fajitas",)
    assert fake_mealie.list_by_tag("no-such-tag") == ()
    # A recipe that lives in Mealie carries its slug, so a pick can be published without re-importing.
    assert fake_mealie.get_recipe("sheet-pan-chicken-fajitas") == sample_recipe.model_copy(
        update={"mealie_slug": "sheet-pan-chicken-fajitas"}
    )
    with pytest.raises(KeyError):
        fake_mealie.get_recipe("no-such-recipe")


def test_fake_mealie_imports_only_known_urls(sample_recipe: RecipeOption) -> None:
    mealie = FakeMealieClient(importable={"https://example.com/r": sample_recipe})
    slug = mealie.import_url("https://example.com/r")
    assert mealie.get_recipe(slug) == sample_recipe.model_copy(update={"mealie_slug": slug})
    with pytest.raises(ValueError):
        mealie.import_url("https://example.com/unscrapeable")


def test_fake_mealie_records_meal_plans(fake_mealie: FakeMealieClient) -> None:
    week = date(2026, 9, 27)
    ref = fake_mealie.set_meal_plan(week, ("sheet-pan-chicken-fajitas",))
    assert ref
    assert fake_mealie.meal_plans[week] == ("sheet-pan-chicken-fajitas",)
    with pytest.raises(KeyError):
        fake_mealie.set_meal_plan(week, ("no-such-recipe",))


# ── FakePantry ───────────────────────────────────────────────────────────────


def test_fake_pantry_staples_due_most_overdue_first(fake_pantry: FakePantry, today: date) -> None:
    assert [i.name for i in fake_pantry.staples_due(today)] == [
        "butter",
        "tahini",
        "olive oil",
    ]


def test_fake_pantry_flips_status_by_alias_case_insensitive(
    fake_pantry: FakePantry, today: date
) -> None:
    flipped = fake_pantry.flip_status("EVOO", "have")
    assert flipped is not None
    assert (flipped.name, flipped.status) == ("olive oil", "have")

    fake_pantry.flip_status("rice", "buy_next_time")
    assert "rice" in [i.name for i in fake_pantry.staples_due(today)]


def test_fake_pantry_staples_due_ignores_non_staples_interval_data(
    sample_pantry_items: tuple[PantryItem, ...], today: date
) -> None:
    # model_construct skips validation, so the perishable can carry a zero interval.
    milk = PantryItem.model_construct(
        id=7, name="milk", category="perishable", typical_interval_days=0, last_purchased=today
    )
    pantry = FakePantry((*sample_pantry_items, milk))

    assert [i.name for i in pantry.staples_due(today)] == ["butter", "tahini", "olive oil"]


def test_fake_pantry_matches_mixed_case_stored_names() -> None:
    pantry = FakePantry(
        (PantryItem(id=1, name="Greek Yogurt", aliases=("Yogurt",), category="perishable"),)
    )
    assert pantry.flip_status("yogurt", "buy_next_time") is not None
    assert pantry.flip_status("greek yogurt", "have") is not None


def test_fake_pantry_flip_unknown_item_returns_none(fake_pantry: FakePantry) -> None:
    assert fake_pantry.flip_status("saffron", "buy_next_time") is None


def test_fake_pantry_does_not_mutate_the_items_it_was_given(
    sample_pantry_items: tuple[PantryItem, ...], fake_pantry: FakePantry
) -> None:
    fake_pantry.flip_status("rice", "buy_next_time")
    assert next(i for i in sample_pantry_items if i.name == "rice").status == "have"


def _staple(id_: int, name: str, **fields: object) -> PantryItem:
    return PantryItem.model_validate({"id": id_, "name": name, "category": "staple", **fields})


def _due(pantry: FakePantry, on: date) -> list[str]:
    return [item.name for item in pantry.staples_due(on)]


def _days(n: int) -> timedelta:
    return timedelta(days=n)


def test_fake_pantry_asks_once_90_percent_of_the_interval_has_passed(today: date) -> None:
    # ceil(0.9 * 11) = 10 days after the last purchase.
    pantry = FakePantry(
        (
            _staple(1, "cumin", typical_interval_days=11, last_purchased=today - _days(10)),
            _staple(2, "paprika", typical_interval_days=11, last_purchased=today - _days(9)),
        )
    )
    assert _due(pantry, today) == ["cumin"]


def test_fake_pantry_next_ask_on_replaces_the_interval_rule(today: date) -> None:
    pantry = FakePantry(
        (
            # Overdue by its interval, but postponed to tomorrow.
            _staple(
                1,
                "tahini",
                typical_interval_days=60,
                last_purchased=today - _days(120),
                next_ask_on=today + _days(1),
            ),
            # Not due by its interval, but its ask date is today.
            _staple(
                2,
                "rice",
                typical_interval_days=56,
                last_purchased=today - _days(1),
                next_ask_on=today,
            ),
            # Already owned, never bought through the system: a bootstrap reminder only.
            _staple(3, "cumin", next_ask_on=today - _days(3)),
        )
    )
    assert _due(pantry, today) == ["cumin", "rice"]


def test_fake_pantry_flag_overrides_a_later_next_ask_on(today: date) -> None:
    pantry = FakePantry(
        (_staple(1, "butter", status="buy_next_time", next_ask_on=today + _days(30)),)
    )
    assert _due(pantry, today) == ["butter"]


def test_fake_pantry_staples_due_order(today: date) -> None:
    """Flagged first (no ask date last among them), then days past the ask date, then name."""
    pantry = FakePantry(
        (
            _staple(1, "Zaatar", next_ask_on=today - _days(2)),
            _staple(2, "allspice", next_ask_on=today - _days(2)),
            _staple(3, "salt", next_ask_on=today - _days(9)),
            _staple(4, "honey", status="buy_next_time"),
            _staple(5, "butter", status="buy_next_time", next_ask_on=today + _days(14)),
            _staple(6, "oats", status="buy_next_time", next_ask_on=today - _days(1)),
        )
    )
    assert _due(pantry, today) == ["oats", "butter", "honey", "salt", "allspice", "Zaatar"]


def test_fake_pantry_gets_items_by_name_or_alias(fake_pantry: FakePantry) -> None:
    for query in ("olive oil", " EVOO ", "Olive Oil"):
        item = fake_pantry.get_item(query)
        assert item is not None
        assert item.id == 1


def test_fake_pantry_matching_casefolds() -> None:
    pantry = FakePantry((PantryItem(id=1, name="Weißwurst", category="perishable"),))
    item = pantry.get_item("WEISSWURST")
    assert item is not None
    assert item.id == 1


# Every by-name method, so none of them drifts to its own matching rule.
BY_NAME: dict[str, Callable[[FakePantry, str, date], PantryItem | None]] = {
    "get_item": lambda pantry, name, on: pantry.get_item(name),
    "flip_status": lambda pantry, name, on: pantry.flip_status(name, "buy_next_time"),
    "confirm_stocked": lambda pantry, name, on: pantry.confirm_stocked(name, on),
    "log_purchase": lambda pantry, name, on: pantry.log_purchase(name, on),
}


@pytest.mark.parametrize("method", list(BY_NAME))
def test_fake_pantry_exact_name_beats_another_items_alias(method: str, today: date) -> None:
    pantry = FakePantry((_staple(1, "brown rice", aliases=("rice",)), _staple(2, "rice")))
    item = BY_NAME[method](pantry, "RICE", today)
    assert item is not None
    assert item.id == 2


@pytest.mark.parametrize("method", list(BY_NAME))
def test_fake_pantry_unknown_name_returns_none_and_writes_nothing(
    method: str, fake_pantry: FakePantry, today: date
) -> None:
    before = fake_pantry.list_items()
    assert BY_NAME[method](fake_pantry, "saffron", today) is None
    assert fake_pantry.list_items() == before


def test_fake_pantry_lists_every_item_in_id_order(
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    assert FakePantry(reversed(sample_pantry_items)).list_items() == sample_pantry_items


def test_fake_pantry_still_good_asks_again_in_a_week(today: date) -> None:
    last = today - _days(30)
    pantry = FakePantry(
        (
            _staple(
                1, "butter", status="buy_next_time", typical_interval_days=21, last_purchased=last
            ),
        )
    )

    item = pantry.confirm_stocked("Butter", today)

    assert item is not None
    assert (item.status, item.next_ask_on, item.last_purchased) == (
        "have",
        today + _days(7),
        last,
    )
    assert pantry.get_item("butter") == item
    assert _due(pantry, today + _days(6)) == []
    assert _due(pantry, today + _days(7)) == ["butter"]
    assert pantry.confirm_stocked("butter", today) == item  # a replay changes nothing


@pytest.mark.parametrize(("interval", "wait"), [(42, 42), (None, 7)])
def test_fake_pantry_plenty_pushes_the_ask_back_one_interval(
    interval: int | None, wait: int, today: date
) -> None:
    pantry = FakePantry((_staple(1, "rice", typical_interval_days=interval),))

    item = pantry.confirm_stocked("rice", today, plenty=True)

    assert item is not None
    assert (item.status, item.next_ask_on) == ("have", today + _days(wait))
    assert pantry.get_item("rice") == item


def test_fake_pantry_logging_the_latest_purchase_resets_the_item(today: date) -> None:
    pantry = FakePantry(
        (
            _staple(
                1,
                "olive oil",
                status="buy_next_time",
                typical_interval_days=70,
                last_purchased=today - _days(65),
                next_ask_on=today + _days(3),
            ),
        )
    )

    item = pantry.log_purchase("olive oil", today, qty=2.5, price_cents=0)

    assert item is not None
    assert (item.last_purchased, item.status, item.next_ask_on) == (today, "have", None)
    assert pantry.get_item("olive oil") == item


def test_fake_pantry_back_dated_purchase_only_adds_history(today: date) -> None:
    before = _staple(
        1,
        "olive oil",
        status="buy_next_time",
        typical_interval_days=70,
        last_purchased=today,
        next_ask_on=today + _days(3),
    )
    pantry = FakePantry((before,))

    item = pantry.log_purchase("olive oil", today - _days(70))

    after = pantry.get_item("olive oil")
    assert item == after
    assert after is not None
    assert (after.last_purchased, after.status, after.next_ask_on) == (
        today,
        "buy_next_time",
        today + _days(3),
    )


def test_fake_pantry_duplicate_purchase_makes_no_writes(today: date) -> None:
    pantry = FakePantry((_staple(1, "rice", typical_interval_days=56),))
    pantry.log_purchase("rice", today)
    postponed = pantry.confirm_stocked("rice", today)

    # A replayed "ordered" message for the same day must not clear the postponement.
    assert pantry.log_purchase("RICE", today) == postponed
    assert pantry.get_item("rice") == postponed


@pytest.mark.parametrize(
    "bad",
    [
        {"qty": 0},
        {"qty": -1},
        {"qty": float("nan")},
        {"qty": float("inf")},
        {"price_cents": -1},
    ],
)
def test_fake_pantry_rejects_bad_purchase_values_before_writing(
    bad: dict[str, Any], fake_pantry: FakePantry, today: date
) -> None:
    before = fake_pantry.list_items()
    with pytest.raises(ValueError):
        fake_pantry.log_purchase("rice", today, **bad)
    with pytest.raises(ValueError):  # arguments are checked before the name lookup
        fake_pantry.log_purchase("saffron", today, **bad)
    assert fake_pantry.list_items() == before


# ── week_start is the Sunday of the cook ─────────────────────────────────────

WEEK_START_OWNERS: dict[type[BaseModel], dict[str, object]] = {
    WeekProposal: _proposal().model_dump(),
    CartList: {"items": ()},
}


@pytest.mark.parametrize("model", list(WEEK_START_OWNERS), ids=lambda m: m.__name__)
def test_week_start_accepts_a_sunday(model: type[BaseModel]) -> None:
    sunday = date(2026, 9, 27)
    built = model.model_validate(WEEK_START_OWNERS[model] | {"week_start": sunday})
    assert built.model_dump()["week_start"] == sunday


@pytest.mark.parametrize("model", list(WEEK_START_OWNERS), ids=lambda m: m.__name__)
@pytest.mark.parametrize(
    ("day", "weekday"), [(date(2026, 9, 26), "Saturday"), (date(2026, 9, 28), "Monday")]
)
def test_week_start_rejects_other_weekdays_and_names_the_date(
    model: type[BaseModel], day: date, weekday: str
) -> None:
    with pytest.raises(
        ValidationError, match=rf"week_start[\s\S]*{day.isoformat()} is a {weekday}"
    ):
        model.model_validate(WEEK_START_OWNERS[model] | {"week_start": day})
