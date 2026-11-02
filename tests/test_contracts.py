import inspect
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from meals import claude_runner
from meals.contracts import (
    AddItemIntent,
    CartItem,
    CartList,
    CartReport,
    ClaudeRunnerError,
    Components,
    ComponentSlot,
    Contract,
    CustodyIntent,
    FindIntent,
    Intent,
    IntentKind,
    MealieClient,
    MealieUnavailable,
    ModeIntent,
    Number,
    Pantry,
    PantryFlipIntent,
    PantryItem,
    PickIntent,
    RateIntent,
    RecipeOption,
    SaveIntent,
    SwapIntent,
    Text,
    WeekProposal,
    describe_rejection,
    validate_claude_output,
)
from meals.fakes import ClaudeCall, FakeClaudeRunner, FakeMealieClient, FakePantry
from meals.pantry import SqlitePantry

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── layering ─────────────────────────────────────────────────────────────────


def test_import_layering_contract_holds() -> None:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run([lint_imports], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# ADR-0001, Ownership → contracts, item 3: top to bottom. `__main__` is the composition root.
ADR_0001_LAYERS = [
    "(__main__)",
    "(bot) | (jobs) | (mcp_tools) | (seed_loader)",
    "(pantry) | (mealie_client) | (search) | (planner) | (cart) | (background) | (plan_state)",
    "(db) | (claude_runner) | (rollup)",
    "contracts | (config)",
]


def _tier(layer: str) -> set[str]:
    """A layer's members as written, "(x)" marking an optional one; sibling order is immaterial."""
    return {member.strip() for member in layer.split("|")}


def test_layers_contract_has_adr_0001s_tiers() -> None:
    """lint-imports passes with the old tiers too (nothing imports across the new ones yet), so
    the tiers themselves are pinned here."""
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]
    # Lanes may add their own layers contracts; this one spans the whole package.
    (layering,) = [
        c for c in contracts if c["type"] == "layers" and c.get("containers") == ["meals"]
    ]

    assert [_tier(layer) for layer in layering["layers"]] == [
        _tier(layer) for layer in ADR_0001_LAYERS
    ]


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
def test_closed_vocabularies_reject_unknown_values(model: Any, field: str, bad: str) -> None:
    # Intent is a union of one model per kind, not a class: validate through a TypeAdapter, from a
    # pick whose args fit.
    base: dict[str, object] = (
        _proposal().model_dump()
        if model is WeekProposal
        else {"kind": "pick", "args": {"numbers": [1]}}
    )
    with pytest.raises(ValidationError):
        TypeAdapter(model).validate_python(base | {field: bad})


def test_models_are_frozen(sample_recipe: RecipeOption) -> None:
    with pytest.raises(ValidationError):
        sample_recipe.name = "something else"  # type: ignore[misc]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Intent).validate_python(
            {"kind": "pick", "args": {"numbers": [1]}, "confidence": 0.9}
        )


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


# ── Intent: one typed model per kind ─────────────────────────────────────────

# Seam map contracts-8-bot-asks (AC-1 to AC-5) and bot's ask (2026-11-02, §1): Intent is a plain
# union of nine `XIntent(kind: Literal["x"], args: XArgs)` models. Per kind: the variant, and args
# that fit, as Claude's JSON carries them.
INTENT_VARIANTS: dict[str, tuple[type[Contract], dict[str, object]]] = {
    "pick": (PickIntent, {"numbers": [2, 3]}),
    "swap": (
        SwapIntent,
        {"slot": "veg", "remove": "peppers + onion", "add": "roasted zucchini"},
    ),
    "custody": (CustodyIntent, {"custody": "wed+sat_sun"}),
    "pantry_flip": (PantryFlipIntent, {"item": "olive oil", "to": "still_good"}),
    "add_item": (AddItemIntent, {"item": "eggs"}),
    "find": (FindIntent, {"request": "a sheet-pan salmon dinner"}),
    "save": (SaveIntent, {"number": 2, "add_to_week": True}),
    "rate": (RateIntent, {"target": "shredded chicken", "rater": "miles", "rating": -1}),
    "mode": (ModeIntent, {"mode": "components"}),
}


class _Reply(Contract):
    """An object holding Intents, as bot's IntentReply does (a bare union can't be the root)."""

    intents: tuple[Intent, ...]


def _intent(payload: object) -> Intent:
    """Validate one Intent the way bot validates Claude's output: wrapped in an object, through
    validate_claude_output."""
    return validate_claude_output(_Reply, {"intents": [payload]}).intents[0]


@pytest.mark.parametrize(
    ("kind", "args"),
    [
        *(pytest.param(kind, args, id=kind) for kind, (_, args) in INTENT_VARIANTS.items()),
        # "cancel the zucchini": add is None, which drops the entry (bot's ask, SwapArgs).
        pytest.param("swap", {"slot": "veg", "remove": "zucchini", "add": None}, id="swap-drop"),
    ],
)
def test_each_intent_kind_validates_to_its_variant(kind: str, args: dict[str, object]) -> None:
    variant, _ = INTENT_VARIANTS[kind]

    intent = _intent({"kind": kind, "args": args})

    assert type(intent) is variant
    assert intent.kind == kind
    assert type(intent.args) is variant.model_fields["args"].annotation
    assert intent.args.model_dump(mode="json") == args
    hash(intent)  # immutable all the way down: a list field (e.g. pick's numbers) is unhashable


# AC-2: each row is the kind's fitting args from INTENT_VARIANTS with one thing changed.
MISFITS = [
    # Another kind's args: kind and args are checked together, not each on its own.
    pytest.param("pick", {"item": "eggs"}, id="pick-with-add_item-args"),
    pytest.param("find", {"item": "eggs"}, id="find-with-add_item-args"),
    pytest.param("save", {"numbers": [2]}, id="save-with-pick-args"),
    # A missing field. SwapArgs.add and SaveArgs.add_to_week are required, with no default.
    pytest.param("pick", {}, id="pick-missing-numbers"),
    pytest.param("swap", {"slot": "veg", "remove": "peppers + onion"}, id="swap-missing-add"),
    pytest.param("save", {"number": 2}, id="save-missing-add_to_week"),
    pytest.param("rate", {"target": "shredded chicken", "rating": -1}, id="rate-missing-rater"),
    # An extra field.
    pytest.param("add_item", {"item": "eggs", "qty": "2"}, id="add_item-extra-field"),
    # A blank Text field.
    pytest.param("add_item", {"item": "   "}, id="add_item-blank-item"),
    pytest.param("find", {"request": "  "}, id="find-blank-request"),
    pytest.param("pantry_flip", {"item": "  ", "to": "still_good"}, id="pantry_flip-blank-item"),
    pytest.param(
        "swap",
        {"slot": "veg", "remove": " ", "add": "roasted zucchini"},
        id="swap-blank-remove",
    ),
    pytest.param(
        "swap", {"slot": "veg", "remove": "peppers + onion", "add": "  "}, id="swap-blank-add"
    ),
    pytest.param("rate", {"target": "\t", "rater": "miles", "rating": -1}, id="rate-blank-target"),
    # A wrongly typed field.
    pytest.param("add_item", {"item": 5}, id="add_item-item-not-text"),
    pytest.param("pick", {"numbers": [2, "three"]}, id="pick-number-not-int"),
    pytest.param("pick", {"numbers": 2}, id="pick-numbers-not-a-list"),
    pytest.param("save", {"number": "two", "add_to_week": True}, id="save-number-not-int"),
    pytest.param("save", {"number": 2, "add_to_week": "maybe"}, id="save-add_to_week-not-bool"),
    # Numbers are 1-based, and a pick names at least one.
    pytest.param("pick", {"numbers": []}, id="pick-no-numbers"),
    pytest.param("pick", {"numbers": [2, 0]}, id="pick-number-0"),
    pytest.param("save", {"number": 0, "add_to_week": True}, id="save-number-0"),
    # Closed vocabularies.
    pytest.param(
        "swap",
        {"slot": "dessert", "remove": "peppers + onion", "add": "roasted zucchini"},
        id="swap-slot-not-a-components-field",
    ),
    # "have" is a PantryStatus, but not one of pantry_flip's three replies.
    pytest.param("pantry_flip", {"item": "olive oil", "to": "have"}, id="pantry_flip-to-have"),
    pytest.param(
        "rate", {"target": "shredded chicken", "rater": "miles", "rating": 0}, id="rate-rating-0"
    ),
    pytest.param(
        "rate",
        {"target": "shredded chicken", "rater": "guest", "rating": -1},
        id="rate-rater-guest",
    ),
    pytest.param("custody", {"custody": "every_day"}, id="custody-not-a-custody"),
    pytest.param("mode", {"mode": "keto"}, id="mode-not-a-plan-mode"),
]


@pytest.mark.parametrize(("kind", "args"), MISFITS)
def test_intent_args_reject_shapes_that_do_not_fit_the_kind(
    kind: str, args: dict[str, object]
) -> None:
    variant, fitting = INTENT_VARIANTS[kind]
    # The fitting args pass, so the misfit is refused for the one thing that changed.
    assert type(_intent({"kind": kind, "args": fitting})) is variant

    with pytest.raises(ValidationError):
        _intent({"kind": kind, "args": args})


@pytest.mark.parametrize(
    ("intent", "named", "hidden"),
    [
        pytest.param(
            {"kind": "swap", "args": {"slot": "dessert", "remove": "peppers + onion", "add": None}},
            "intents.0.SwapIntent.args.slot",
            "PickIntent",
            id="swap-slot",
        ),
        # A late branch, so the reason can't depend on the union's order.
        pytest.param(
            {
                "kind": "rate",
                "args": {"target": "shredded chicken", "rater": "guest", "rating": -1},
            },
            "intents.0.RateIntent.args.rater",
            "PickIntent",
            id="rate-rater",
        ),
        # No branch's kind matches, so there's no branch to prefer; the reason still names kind.
        pytest.param(
            {"kind": "dance", "args": {"item": "eggs"}}, "kind", None, id="no-kind-matches"
        ),
    ],
)
def test_rejection_names_the_field_in_the_branch_whose_kind_matched(
    intent: dict[str, object], named: str, hidden: str | None
) -> None:
    """describe_rejection exists so a refused field (a possible injection) stands out in the logs.
    A misfit Intent fails in every union branch, and the others fail only on kind, so the reason
    names the error in the branch whose kind matched."""
    with pytest.raises(ValidationError) as caught:
        _Reply.model_validate({"intents": [intent]})

    reason = describe_rejection(_Reply, caught.value)

    assert "error(s); first at " in reason, reason
    assert named in reason, reason
    if hidden is not None:
        assert hidden not in reason, reason


def test_intent_kind_names_every_variant() -> None:
    """IntentKind stays the closed list of kinds, and matches the union's, so neither drifts."""
    kinds = [get_args(v.model_fields["kind"].annotation)[0] for v in get_args(Intent)]

    assert len(kinds) == len(set(kinds))  # no two variants share a kind
    assert set(kinds) == set(get_args(IntentKind))


def test_component_slot_names_every_components_field() -> None:
    assert sorted(get_args(ComponentSlot)) == sorted(Components.model_fields)


def test_intent_variants_are_frozen_and_reject_extras() -> None:
    """Contract is frozen and refuses extra fields (test_models_are_frozen,
    test_models_reject_unknown_fields), so each variant and its args must be one, and must not
    override that config."""
    for variant in get_args(Intent):
        for model in (variant, variant.model_fields["args"].annotation):
            assert issubclass(model, Contract), model
            assert model.model_config.get("frozen") is True, model
            assert model.model_config.get("extra") == "forbid", model


def test_text_strips_and_refuses_blank() -> None:
    text = TypeAdapter(Text)

    assert text.validate_python("  olive oil\n") == "olive oil"
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValidationError):
            text.validate_python(blank)
    # The args fields use it: Steve's " eggs " arrives stripped.
    intent = _intent({"kind": "add_item", "args": {"item": " eggs "}})
    assert isinstance(intent, AddItemIntent)
    assert intent.args.item == "eggs"
    # Number, its sibling alias: 1-based.
    number = TypeAdapter(Number)
    assert number.validate_python(1) == 1
    with pytest.raises(ValidationError):
        number.validate_python(0)


def test_intent_schema_is_one_the_claude_cli_accepts() -> None:
    """The claude CLI refuses pydantic's `discriminator` keyword at startup, before any model call
    (.brain/research/claude-cli-json-schema-subset.md, checked 2026-11-02 on 2.1.314). So Intent is
    a plain union (anyOf, never oneOf), and each variant pins its kind for Claude to pick by."""
    schema = _Reply.model_json_schema()
    text = json.dumps(schema)
    kinds = [
        definition["properties"]["kind"]
        for definition in schema.get("$defs", {}).values()
        if "kind" in definition.get("properties", {})
    ]

    assert '"discriminator":' not in text
    assert '"oneOf":' not in text
    assert all("const" in kind or len(kind.get("enum", ())) == 1 for kind in kinds), kinds
    pinned = [kind["const"] if "const" in kind else kind["enum"][0] for kind in kinds]
    assert sorted(pinned) == sorted(INTENT_VARIANTS)


# ── fakes satisfy the Protocols ──────────────────────────────────────────────


def test_fakes_implement_protocols(fake_mealie: FakeMealieClient, fake_pantry: FakePantry) -> None:
    assert isinstance(fake_mealie, MealieClient)
    assert isinstance(fake_pantry, Pantry)


def _public_methods(cls: type) -> dict[str, inspect.Signature]:
    """Inherited ones included, so splitting a class into bases can't hide a method."""
    return {
        name: inspect.signature(member, eval_str=True)
        for name, member in inspect.getmembers(cls, callable)
        if not name.startswith("_")
    }


def test_both_pantries_have_every_pantry_method_with_the_same_signature() -> None:
    """Callers hold a `Pantry`, and isinstance checks method names only. So the fake offers
    exactly the Protocol's methods (one only the fake has is a contract gap), and SqlitePantry
    offers them all (plus its own `load_seed`), with the same names, kinds, defaults and types."""
    protocol = _public_methods(Pantry)
    assert _public_methods(FakePantry) == protocol
    assert _public_methods(SqlitePantry).items() >= protocol.items()


# ── FakeClaudeRunner ─────────────────────────────────────────────────────────


def test_fake_claude_returns_queued_responses_in_order_and_records_calls() -> None:
    fake = FakeClaudeRunner([{"a": 1}, {"a": 2}])
    assert fake.run("first") == {"a": 1}
    assert fake.run("second", chrome=True, timeout=30) == {"a": 2}
    assert [(c.prompt, c.chrome, c.timeout) for c in fake.calls] == [
        ("first", False, 600),
        ("second", True, 30),
    ]


def test_fake_claude_records_hold_fds_with_the_real_runs_default() -> None:
    """ADR-0001: cart passes its locks as run(..., hold_fds=...), and its tests check they were
    passed. ClaudeCall built without hold_fds, as before, gets the same default."""
    fake = FakeClaudeRunner([{"a": 1}, {"a": 2}])
    fake.run("fill the cart", chrome=True, hold_fds=(7, 9))
    fake.run("find recipes")

    assert [call.hold_fds for call in fake.calls] == [(7, 9), ()]
    assert ClaudeCall("find recipes", None, False, 600).hold_fds == ()


def test_fake_claude_records_web() -> None:
    """`run(..., web=True)`: bot's parse run passes web=False, and its tests check it did.
    ClaudeCall built without web, as before, gets the real run's default."""
    fake = FakeClaudeRunner([{"a": 1}, {"a": 2}])
    fake.run("parse Steve's reply", web=False)
    fake.run("find recipes")

    assert [call.web for call in fake.calls] == [False, True]
    assert ClaudeCall("find recipes", None, False, 600).web is True


def test_fake_claude_run_takes_the_real_runs_parameters() -> None:
    """`patched_claude` (tests/conftest.py) swaps the fake in for claude_runner.run, so a call
    written against the real signature must bind to the fake's: same names, kinds and defaults."""

    def shape(run: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
        return [(p.name, p.kind, p.default) for p in inspect.signature(run).parameters.values()]

    assert shape(FakeClaudeRunner().run) == shape(claude_runner.run)


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


def test_fake_mealie_refuses_slugs_mealie_would_never_issue(sample_recipe: RecipeOption) -> None:
    """The real client treats a slug outside [A-Za-z0-9_-]+ as unknown; the fake can't hold one."""
    with pytest.raises(ValueError, match="slug"):
        FakeMealieClient(recipes={"Bad Slug/..": sample_recipe})
    unsluggable = sample_recipe.model_copy(update={"name": "!!!"})
    mealie = FakeMealieClient(importable={"https://example.com/p": unsluggable})
    with pytest.raises(ValueError, match="slug"):
        mealie.import_url("https://example.com/p")
    with pytest.raises(ValueError, match="slug"):
        FakeMealieClient(unavailable=("Bad Slug/..",))


def test_mealie_unavailable_is_not_caught_as_a_missing_recipe() -> None:
    """A caller skipping missing recipes with `except KeyError` must not swallow an outage."""
    assert issubclass(MealieUnavailable, Exception)
    assert not issubclass(MealieUnavailable, LookupError)


@pytest.mark.parametrize("stored", [True, False], ids=["stored", "not-stored"])
def test_fake_mealie_can_fail_one_recipe_among_good_ones(
    sample_recipe: RecipeOption, stored: bool
) -> None:
    """One broken rotation favourite fails on its own while the rest still load: the case search's
    _rotation_pool is to skip. It fails whether or not the fake holds a recipe for it."""
    recipes = {"good": sample_recipe} | ({"broken": sample_recipe} if stored else {})
    mealie = FakeMealieClient(
        recipes=recipes, tags={"rotation": ("good", "broken")}, unavailable=("broken",)
    )
    assert mealie.list_by_tag("rotation") == ("good", "broken")
    with pytest.raises(MealieUnavailable, match="broken"):
        mealie.get_recipe("broken")
    assert mealie.get_recipe("good").mealie_slug == "good"
    with pytest.raises(KeyError):
        mealie.get_recipe("no-such-recipe")


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


def test_fake_pantry_next_ask_on_is_a_floor_under_the_interval_rule(today: date) -> None:
    """Steve, 2026-11-01: the ask date is the later of `next_ask_on` and the 90% point."""
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
            # Its next_ask_on is today, but its 90% point is day 50 (ceil(0.9 * 56) after day -1).
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
    assert _due(pantry, today) == ["cumin"]
    assert _due(pantry, today + _days(1)) == ["cumin", "tahini"]
    assert "rice" not in _due(pantry, today + _days(49))
    assert "rice" in _due(pantry, today + _days(50))


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
            _staple(7, " cumin", next_ask_on=today - _days(2)),  # names compare after strip
        )
    )
    assert _due(pantry, today) == [
        "oats",
        "butter",
        "honey",
        "salt",
        "allspice",
        " cumin",
        "Zaatar",
    ]


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


def test_fake_pantry_matching_ignores_spaces_around_stored_names_and_aliases() -> None:
    pantry = FakePantry(
        (PantryItem(id=1, name=" olive oil ", aliases=(" EVOO ",), category="staple"),)
    )
    for query in ("olive oil", "evoo"):
        item = pantry.get_item(query)
        assert item is not None, query
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


@pytest.mark.parametrize(
    ("interval", "wait"),
    [(42, 42), (None, 7), (3, 7)],
    ids=["one interval", "no interval: a week", "short interval: still a week"],
)
def test_fake_pantry_plenty_pushes_the_ask_back_one_interval_but_at_least_a_week(
    interval: int | None, wait: int, today: date
) -> None:
    """Steve, 2026-09-26: "have plenty" never asks sooner than "still good"."""
    pantry = FakePantry((_staple(1, "rice", typical_interval_days=interval),))

    item = pantry.confirm_stocked("rice", today, plenty=True)

    assert item is not None
    assert (item.status, item.next_ask_on) == ("have", today + _days(wait))
    assert pantry.get_item("rice") == item


def test_fake_pantry_still_good_never_pulls_the_ask_earlier(today: date) -> None:
    """PLAN: "still good" pushes the next ask back. Volunteered early, it must not bring it forward:
    it stores a week's floor, and the later 90% point still decides the ask date."""
    last = today - _days(10)
    pantry = FakePantry(
        (
            _staple(
                1,
                "olive oil",
                status="buy_next_time",
                typical_interval_days=70,
                last_purchased=last,
            ),
        )
    )

    item = pantry.confirm_stocked("olive oil", today)

    assert item is not None
    assert (item.status, item.next_ask_on) == ("have", today + _days(7))
    assert pantry.get_item("olive oil") == item
    assert _due(pantry, last + _days(62)) == []
    assert _due(pantry, last + _days(63)) == ["olive oil"]  # the 90% point, not today + 7


@pytest.mark.parametrize(
    ("plenty", "interval", "bought", "stored", "asked"),
    [
        (False, 10, -2, 7, 7),  # ties its 90% point (day 7)
        (True, 20, 30, 20, 48),  # a stale "plenty" (`on` before the last purchase): day 48 asks
        (True, 56, -20, 56, 56),  # plenty beats the 90% point (day 31)
    ],
    ids=["still good ties", "stale plenty", "plenty wins"],
)
def test_fake_pantry_confirming_always_stores_the_push_as_a_floor(
    plenty: bool, interval: int, bought: int, stored: int, asked: int, today: date
) -> None:
    """Steve, 2026-11-01: the push is stored even under a later 90% point, which still asks."""
    last = today + _days(bought)
    pantry = FakePantry(
        (
            _staple(
                1,
                "rice",
                status="buy_next_time",
                typical_interval_days=interval,
                last_purchased=last,
            ),
        )
    )

    item = pantry.confirm_stocked("rice", today, plenty=plenty)

    assert item is not None
    assert (item.status, item.next_ask_on) == ("have", today + _days(stored))
    assert pantry.get_item("rice") == item
    assert _due(pantry, today + _days(asked - 1)) == []
    assert _due(pantry, today + _days(asked)) == ["rice"]
    assert pantry.confirm_stocked("rice", today, plenty=plenty) == item  # a replay changes nothing


def test_fake_pantry_still_good_holds_a_week_when_the_interval_later_shrinks(today: date) -> None:
    """Steve, 2026-11-01: after "still good", don't ask again inside the week. A back-dated purchase
    can make the real pantry re-learn a shorter interval; the fake leaves intervals as given, so the
    test rebuilds the item with one."""
    pantry = FakePantry(
        (_staple(1, "coffee", typical_interval_days=60, last_purchased=today - _days(10)),)
    )
    pantry.confirm_stocked("coffee", today)  # the 90% point (day 44) is later than the push
    backdated = pantry.log_purchase("coffee", today - _days(14))  # only adds history

    assert backdated is not None
    shrunk = FakePantry((backdated.model_copy(update={"typical_interval_days": 4}),))
    assert _due(shrunk, today + _days(6)) == []  # the new 90% point is day -6
    assert _due(shrunk, today + _days(7)) == ["coffee"]


def test_fake_pantry_takes_a_datetime_as_its_calendar_day(today: date) -> None:
    """SqlitePantry does too: the schema stores DATE."""
    late = datetime.combine(today, time(23, 30))
    pantry = FakePantry(
        (_staple(1, "rice", typical_interval_days=56), _staple(2, "butter", next_ask_on=today))
    )

    assert _due(pantry, late) == ["butter"]
    confirmed = pantry.confirm_stocked("rice", late)
    bought = pantry.log_purchase("butter", late)

    assert confirmed is not None and bought is not None
    assert type(confirmed.next_ask_on) is date and confirmed.next_ask_on == today + _days(7)
    assert type(bought.last_purchased) is date and bought.last_purchased == today


@pytest.mark.parametrize("plenty", [False, True], ids=["still_good", "plenty"])
@pytest.mark.parametrize(
    ("stored", "kept"),
    [(90, True), (3, False)],
    ids=["a later stored date stays", "an earlier stored date rises to the push"],
)
def test_fake_pantry_confirming_stores_the_later_of_stored_and_pushed(
    plenty: bool, stored: int, kept: bool, today: date
) -> None:
    pantry = FakePantry(
        (_staple(1, "rice", typical_interval_days=42, next_ask_on=today + _days(stored)),)
    )

    item = pantry.confirm_stocked("rice", today, plenty=plenty)

    pushed = today + _days(42 if plenty else 7)
    assert item is not None
    assert item.next_ask_on == (today + _days(stored) if kept else pushed)


def test_pantry_item_schema_says_next_ask_on_is_a_floor() -> None:
    """The pantry MCP tools' output schemas carry this to Claude: a set next_ask_on is a floor,
    not the ask date, so Claude mustn't tell Steve it's when he'll be asked next."""
    field = PantryItem.model_json_schema()["properties"]["next_ask_on"]
    assert "floor" in field.get("description", "").lower()


def test_fake_pantry_refuses_items_the_real_schema_cannot_hold() -> None:
    with pytest.raises(ValueError, match="id"):
        FakePantry((_staple(1, "rice"), _staple(1, "oats")))
    with pytest.raises(ValueError, match="name"):  # names are unique after strip and casefold
        FakePantry((_staple(1, "Jalapeño"), _staple(2, "JALAPEÑO")))
    with pytest.raises(ValueError, match="name"):
        FakePantry((_staple(1, "rice"), _staple(2, " rice ")))


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
    postponed = pantry.confirm_stocked("rice", today, plenty=True)
    assert postponed is not None and postponed.next_ask_on == today + _days(56)

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
