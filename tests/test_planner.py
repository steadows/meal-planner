import logging
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from meals import planner
from meals.config import get_settings
from meals.contracts import (
    ClaudeRunnerError,
    Components,
    PantryItem,
    RecipeOption,
    WeekProposal,
)
from meals.fakes import FakeClaudeRunner, FakeMealieClient, FakePantry

WEEK = date(2026, 9, 27)  # the Sunday after conftest's TODAY
FAVORITE = "sheet-pan-chicken-fajitas"  # tagged "rotation" in conftest's fake_mealie

pytestmark = pytest.mark.usefixtures("prefs_file")


@pytest.fixture
def new_recipe(sample_recipe: RecipeOption) -> RecipeOption:
    return sample_recipe.model_copy(
        update={"name": "Turkey meatballs in red sauce", "url": "https://example.com/meatballs"}
    )


def _draft(new_recipe: RecipeOption, **overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "favorites": [FAVORITE],
        "new_recipes": [new_recipe.model_dump(mode="json")],
        "components": {
            "proteins": ["shredded chicken", "hard-boiled eggs"],
            "grains": ["brown rice"],
            "veg": ["sweet potato + broccoli"],
            "sauces": ["lemon-tahini"],
            "fresh": ["spinach"],
        },
        "lunch_builds": ["Chicken sweet-potato bowl", "Turkey taco plate"],
        "kid_nights": ["Wed cook-with-Miles (ravioli)", "Fri from the batch", "Sat freezer"],
    }
    return draft | overrides


def _propose(
    mealie: FakeMealieClient,
    pantry: FakePantry,
    *,
    recent: tuple[WeekProposal, ...] = (),
    **kwargs: Any,
) -> WeekProposal:
    args: dict[str, Any] = {"custody": "wed+fri_sat"} | kwargs
    return planner.propose(WEEK, recent=recent, pantry=pantry, mealie=mealie, **args)


# ── the proposal ─────────────────────────────────────────────────────────────


def test_week_custody_and_mode_come_from_the_caller(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    proposal = _propose(fake_mealie, fake_pantry, custody="wed+sat_sun", mode="recipes")

    assert (proposal.week_start, proposal.custody, proposal.mode) == (
        WEEK,
        "wed+sat_sun",
        "recipes",
    )


def test_mode_defaults_to_mix(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    assert _propose(fake_mealie, fake_pantry).mode == "mix"


def test_claude_cannot_set_week_custody_or_mode(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe, custody="wed+sat_sun"))

    with pytest.raises(ClaudeRunnerError):
        _propose(fake_mealie, fake_pantry)


def test_claude_picks_components_lunches_and_kid_nights(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    draft = _draft(new_recipe)
    patched_claude.queue(draft)

    proposal = _propose(fake_mealie, fake_pantry)

    assert proposal.components == Components.model_validate(draft["components"])
    assert proposal.lunch_builds == tuple(draft["lunch_builds"])
    assert proposal.kid_nights == tuple(draft["kid_nights"])


def test_is_one_structured_web_run_not_chrome(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    _propose(fake_mealie, fake_pantry)

    (call,) = patched_claude.calls
    assert call.schema is not None
    assert call.chrome is False


def test_week_start_must_be_a_sunday(
    patched_claude: FakeClaudeRunner, fake_mealie: FakeMealieClient, fake_pantry: FakePantry
) -> None:
    with pytest.raises(ValueError, match="Sunday"):
        planner.propose(
            WEEK - timedelta(days=1),
            "wed+fri_sat",
            recent=(),
            pantry=fake_pantry,
            mealie=fake_mealie,
        )

    assert patched_claude.calls == []


def test_runner_failure_propagates(
    patched_claude: FakeClaudeRunner, fake_mealie: FakeMealieClient, fake_pantry: FakePantry
) -> None:
    patched_claude.queue(ClaudeRunnerError("claude exited with status 1"))

    with pytest.raises(ClaudeRunnerError):
        _propose(fake_mealie, fake_pantry)


# ── recipe options: favorites from the rotation pool, plus new finds ─────────


def test_favorites_are_the_exact_mealie_recipes_then_new_finds(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    sample_recipe: RecipeOption,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    proposal = _propose(fake_mealie, fake_pantry)

    assert proposal.recipe_options == (sample_recipe, new_recipe)


def test_a_favorite_outside_the_pool_is_dropped_with_a_warning(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    sample_recipe: RecipeOption,
    new_recipe: RecipeOption,
    caplog: pytest.LogCaptureFixture,
) -> None:
    patched_claude.queue(_draft(new_recipe, favorites=["made-up-slug", FAVORITE]))

    with caplog.at_level(logging.WARNING, logger="meals.planner"):
        proposal = _propose(fake_mealie, fake_pantry)

    assert proposal.recipe_options == (sample_recipe, new_recipe)
    assert "made-up-slug" in caplog.text


def test_prompt_lists_only_the_rotation_pool(
    patched_claude: FakeClaudeRunner,
    fake_pantry: FakePantry,
    sample_recipe: RecipeOption,
    new_recipe: RecipeOption,
) -> None:
    untagged = sample_recipe.model_copy(update={"name": "Untagged casserole"})
    mealie = FakeMealieClient(
        recipes={FAVORITE: sample_recipe, "untagged-casserole": untagged},
        tags={planner.ROTATION_TAG: (FAVORITE,)},
    )
    patched_claude.queue(_draft(new_recipe))

    _propose(mealie, fake_pantry)

    (call,) = patched_claude.calls
    assert FAVORITE in call.prompt
    assert sample_recipe.name in call.prompt
    assert "untagged-casserole" not in call.prompt


def test_an_empty_pool_gives_only_new_finds(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry, new_recipe: RecipeOption
) -> None:
    patched_claude.queue(_draft(new_recipe, favorites=[]))

    proposal = _propose(FakeMealieClient(), fake_pantry)

    assert proposal.recipe_options == (new_recipe,)


# ── pantry questions: computed, not Claude's ─────────────────────────────────


def test_pantry_questions_are_the_most_overdue_staples(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    proposal = _propose(fake_mealie, fake_pantry)

    # conftest: butter is flagged, tahini is at 2x its interval, olive oil just past 90%.
    assert proposal.pantry_questions == ("butter", "tahini", "olive oil")


def test_pantry_questions_are_capped_at_three(
    patched_claude: FakeClaudeRunner, fake_mealie: FakeMealieClient, new_recipe: RecipeOption
) -> None:
    flagged = FakePantry(
        PantryItem(id=n, name=f"staple {n}", category="staple", status="buy_next_time")
        for n in range(5)
    )
    patched_claude.queue(_draft(new_recipe))

    assert len(_propose(fake_mealie, flagged).pantry_questions) == 3


def test_no_pantry_questions_when_nothing_is_due(
    patched_claude: FakeClaudeRunner, fake_mealie: FakeMealieClient, new_recipe: RecipeOption
) -> None:
    patched_claude.queue(_draft(new_recipe))

    assert _propose(fake_mealie, FakePantry()).pantry_questions == ()


# ── what Claude is told ──────────────────────────────────────────────────────


def test_prompt_carries_week_custody_mode_and_profile(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    new_recipe: RecipeOption,
    prefs_file: Path,
) -> None:
    patched_claude.queue(_draft(new_recipe))

    _propose(fake_mealie, fake_pantry, custody="wed+sat_sun", mode="components")

    (call,) = patched_claude.calls
    profile = prefs_file.read_text(encoding="utf-8")
    for expected in (WEEK.isoformat(), "wed+sat_sun", "components", profile):
        assert expected in call.prompt


def test_prompt_carries_recent_weeks(
    patched_claude: FakeClaudeRunner,
    fake_mealie: FakeMealieClient,
    fake_pantry: FakePantry,
    sample_recipe: RecipeOption,
    new_recipe: RecipeOption,
) -> None:
    last_week = WeekProposal(
        week_start=WEEK - timedelta(days=7),
        custody="wed+fri_sat",
        recipe_options=(sample_recipe,),
        components=Components(veg=("roasted zucchini",), sauces=("peanut-lime",)),
        lunch_builds=("Egg and veg bowl",),
        kid_nights=(),
        pantry_questions=(),
    )
    patched_claude.queue(_draft(new_recipe))

    _propose(fake_mealie, fake_pantry, recent=(last_week,))

    (call,) = patched_claude.calls
    for expected in ("2026-09-20", "roasted zucchini", "peanut-lime", "Egg and veg bowl"):
        assert expected in call.prompt
    assert sample_recipe.name in call.prompt


# ── live ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_propose_live_against_real_claude(
    monkeypatch: pytest.MonkeyPatch, fake_mealie: FakeMealieClient, fake_pantry: FakePantry
) -> None:
    monkeypatch.delenv("PREFS_FILE")
    get_settings.cache_clear()
    if shutil.which(get_settings().claude_bin) is None:
        pytest.skip("claude CLI not installed")

    proposal = planner.propose(
        WEEK, "wed+fri_sat", recent=(), pantry=fake_pantry, mealie=fake_mealie
    )

    assert proposal.recipe_options
    assert proposal.components.proteins and proposal.components.veg
    assert len(proposal.lunch_builds) == 2
    assert proposal.kid_nights
