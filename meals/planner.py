"""The Saturday planner: recent weeks + profile + pantry + the Mealie rotation pool give next week's
WeekProposal, in one web-enabled `claude -p` run (PLAN.md → Phase 5, Food system).

Claude chooses the food. Everything that doesn't need judgment is set in code: the week, custody
and mode come from the caller, favorites resolve to the exact Mealie recipes, the pantry
questions are the most overdue staples, and the week's shape (option count per mode, a dinner for
each of Miles's nights, the rotation pool) is checked inside validation, so the runner retries a
draft that doesn't fit.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import ClassVar, Literal, Self, get_args

from pydantic import Field, model_validator

from meals import claude_runner
from meals.config import get_settings
from meals.contracts import (
    MAX_PANTRY_QUESTIONS,
    Components,
    Contract,
    Custody,
    MealieClient,
    Pantry,
    PlanMode,
    RecipeOption,
    WeekProposal,
)

logger = logging.getLogger(__name__)

ROTATION_TAG = "rotation"
RECENT_WEEKS = 3
PROPOSE_TIMEOUT_S = 900  # the heaviest run: several recipe pages plus a whole week
_SUNDAY = 6
_REQUIRED_SLOTS = ("proteins", "grains", "veg", "sauces")  # every lunch build needs each
# Recipe options (favorites + new finds) per mode, per PLAN.md → Phase 5 and the mode table.
_OPTIONS_BY_MODE: dict[PlanMode, tuple[int, int]] = {
    "mix": (4, 5),
    "recipes": (4, 5),
    "components": (0, 0),
}
# Dinners Miles is unambiguously here for. Whether wed+sat_sun's Sunday is the cook day or the
# next one is an open question for Steve, so Sunday is not required.
_KID_DINNERS_BY_CUSTODY: dict[Custody, frozenset[str]] = {
    "wed+fri_sat": frozenset({"Wed", "Fri"}),
    "wed+sat_sun": frozenset({"Wed", "Sat"}),
}

Weekday = Literal["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class _KidMeal(Contract):
    day: Weekday
    meal: Literal["lunch", "dinner"]
    plan: str = Field(description="What Miles eats, e.g. 'from the batch: chicken, rice'")


class _Draft(Contract):
    """What Claude decides. Everything else in the WeekProposal is set by `propose`.

    The ClassVars are this week's rules; `_draft_model` sets them on a per-call subclass.
    """

    pool_slugs: ClassVar[frozenset[str]] = frozenset()
    option_count: ClassVar[tuple[int, int]] = (0, 0)
    kid_dinners: ClassVar[frozenset[str]] = frozenset()

    favorites: tuple[str, ...] = Field(description="Slugs copied exactly from the rotation pool")
    new_recipes: tuple[RecipeOption, ...] = Field(description="Fresh finds from the web")
    components: Components = Field(
        description="At least one each of proteins, grains, veg and sauces"
    )
    lunch_builds: tuple[str, ...] = Field(
        min_length=2, max_length=2, description="The two lunch build names picked"
    )
    kid_meals: tuple[_KidMeal, ...] = Field(
        min_length=1, description="One entry per meal Miles eats here this week"
    )

    @model_validator(mode="after")
    def _fits_the_week(self) -> Self:
        problems = []
        if missing := [slot for slot in _REQUIRED_SLOTS if not getattr(self.components, slot)]:
            problems.append(f"components have no {', '.join(missing)}")
        if unknown := sorted(set(self.favorites) - self.pool_slugs):
            problems.append(f"favorites not in the rotation pool: {', '.join(unknown)}")
        low, high = self.option_count
        if not low <= len(set(self.favorites)) + len(self.new_recipes) <= high:
            problems.append(f"need {low}-{high} recipe options in all")
        dinners = {meal.day for meal in self.kid_meals if meal.meal == "dinner"}
        if uncovered := sorted(self.kid_dinners - dinners):
            problems.append(f"no kid dinner for {', '.join(uncovered)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _draft_model(
    pool: Mapping[str, RecipeOption], mode: PlanMode, custody: Custody
) -> type[_Draft]:
    """A _Draft carrying this week's rules, so a draft that breaks them fails validation."""

    class WeekDraft(_Draft):
        pool_slugs = frozenset(pool)
        option_count = _OPTIONS_BY_MODE[mode]
        kid_dinners = _KID_DINNERS_BY_CUSTODY[custody]

    return WeekDraft


def propose(
    week_start: date,
    custody: Custody,
    *,
    mode: PlanMode = "mix",
    recent: Sequence[WeekProposal],
    pantry: Pantry,
    mealie: MealieClient,
) -> WeekProposal:
    """Draft the week whose Sunday cook is `week_start`.

    `recent` holds past weeks as cooked: each narrowed to Steve's picks, as plan_state stores them
    after approval. Any order; the newest RECENT_WEEKS are used.

    Raises ValueError for a non-Sunday `week_start` or an unknown custody or mode, before any
    Claude run; OSError if the profile can't be read; ClaudeRunnerError if the run fails or its
    draft doesn't fit the week (after the runner's one retry).
    """
    if week_start.weekday() != _SUNDAY:
        raise ValueError(f"week_start {week_start} is not a Sunday")
    if custody not in get_args(Custody):
        raise ValueError(f"unknown custody {custody!r}")
    if mode not in get_args(PlanMode):
        raise ValueError(f"unknown plan mode {mode!r}")
    pool = {} if mode == "components" else _rotation_pool(mealie)
    weeks = sorted(recent, key=lambda week: week.week_start, reverse=True)[:RECENT_WEEKS]
    logger.info(
        "planner: proposing %s (%s, %s) from %d favorites and %d recent weeks",
        week_start,
        custody,
        mode,
        len(pool),
        len(weeks),
    )
    prompt = claude_runner.load_prompt(
        "planner",
        "propose",
        week_start=week_start.isoformat(),
        custody=custody,
        mode=mode,
        prefs=get_settings().prefs_file.read_text(encoding="utf-8"),
        recent=_recent_digest(weeks),
        pool=_pool_digest(pool),
    )
    schema = _draft_model(pool, mode, custody)
    draft = claude_runner.run(prompt, schema=schema, timeout=PROPOSE_TIMEOUT_S)
    return WeekProposal(
        week_start=week_start,
        custody=custody,
        mode=mode,
        recipe_options=_favorites(draft.favorites, pool) + draft.new_recipes,
        components=draft.components,
        lunch_builds=draft.lunch_builds,
        kid_nights=tuple(f"{m.day} {m.meal}: {m.plan}" for m in draft.kid_meals),
        pantry_questions=_pantry_questions(pantry, week_start),
    )


def _pantry_questions(pantry: Pantry, week_start: date) -> tuple[str, ...]:
    """The most overdue staples, by name. Not Claude's call: it's a ranking the pantry owns."""
    return tuple(item.name for item in pantry.staples_due(week_start)[:MAX_PANTRY_QUESTIONS])


def _rotation_pool(mealie: MealieClient) -> dict[str, RecipeOption]:
    """Slug -> recipe for every `rotation` favorite. A slug whose recipe is gone is skipped."""
    pool: dict[str, RecipeOption] = {}
    for slug in mealie.list_by_tag(ROTATION_TAG):
        try:
            pool[slug] = mealie.get_recipe(slug)
        except KeyError:
            logger.warning("planner: rotation slug %r has no recipe; skipping it", slug)
    return pool


def _favorites(slugs: Sequence[str], pool: Mapping[str, RecipeOption]) -> tuple[RecipeOption, ...]:
    """Each picked favorite once. Validation has already checked every slug is in the pool."""
    return tuple(pool[slug] for slug in dict.fromkeys(slugs))


def _data(text: str) -> str:
    """Stored text as one inert line: no line breaks or tag brackets to escape its prompt block.
    Recipe names come from imported web pages, so they're untrusted."""
    return " ".join(text.split()).replace("<", "‹").replace(">", "›")


def _recent_digest(recent: Sequence[WeekProposal]) -> str:
    if not recent:
        return "(none yet)"
    return "\n".join(_week_line(week) for week in recent)


def _week_line(week: WeekProposal) -> str:
    slots = {
        "recipes": [r.name for r in week.recipe_options],
        **week.components.model_dump(),
        "lunches": week.lunch_builds,
    }
    parts = "; ".join(
        f"{slot}: {', '.join(_data(name) for name in names)}"
        for slot, names in slots.items()
        if names
    )
    return f"- {week.week_start.isoformat()} ({week.mode}): {parts}"


def _pool_digest(pool: Mapping[str, RecipeOption]) -> str:
    if not pool:
        return "(none)"
    return "\n".join(_pool_line(slug, recipe) for slug, recipe in pool.items())


def _pool_line(slug: str, recipe: RecipeOption) -> str:
    minutes = "?" if recipe.hands_on_min is None else recipe.hands_on_min
    batch = "batch-ok" if recipe.batch_ok else "batch not marked"
    ingredients = ", ".join(_data(ingredient.name) for ingredient in recipe.ingredients)
    return (
        f"- {slug}: {_data(recipe.name)} (hands-on {minutes} min, {batch}); "
        f"ingredients: {ingredients}"
    )
