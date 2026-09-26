import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from meals.contracts import (
    CartItem,
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
@pytest.mark.parametrize("url", [None, "https://www.meijer.com/shopping/p/eggs/123.html"])
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
    assert fake_mealie.get_recipe("sheet-pan-chicken-fajitas") == sample_recipe
    with pytest.raises(KeyError):
        fake_mealie.get_recipe("no-such-recipe")


def test_fake_mealie_imports_only_known_urls(sample_recipe: RecipeOption) -> None:
    mealie = FakeMealieClient(importable={"https://example.com/r": sample_recipe})
    slug = mealie.import_url("https://example.com/r")
    assert mealie.get_recipe(slug) == sample_recipe
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
