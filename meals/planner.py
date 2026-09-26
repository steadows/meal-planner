"""The Saturday planner: recent weeks + profile + pantry + the Mealie rotation pool give next week's
WeekProposal, in one web-enabled `claude -p` run (PLAN.md → Phase 5, Food system).

Claude chooses the food. Everything that doesn't need judgment is set in code: the week, custody
and mode come from the caller, favorites resolve to the exact Mealie recipes, and the pantry
questions are the most overdue staples.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import get_args

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


class _Draft(Contract):
    """What Claude decides. Everything else in the WeekProposal is set by `propose`."""

    favorites: tuple[str, ...] = Field(description="Slugs copied exactly from the rotation pool")
    new_recipes: tuple[RecipeOption, ...] = Field(description="Fresh finds from the web")
    components: Components = Field(
        description="At least one each of proteins, grains, veg and sauces"
    )
    lunch_builds: tuple[str, ...] = Field(
        min_length=2, max_length=2, description="The two lunch build names picked"
    )
    kid_nights: tuple[str, ...] = Field(
        min_length=1, description="One line per night Miles is here"
    )

    @model_validator(mode="after")
    def _components_cover_the_lunch_builds(self) -> "_Draft":
        missing = [slot for slot in _REQUIRED_SLOTS if not getattr(self.components, slot)]
        if missing:
            raise ValueError(f"components have no {', '.join(missing)}")
        return self


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
    Claude run; OSError if the profile can't be read; ClaudeRunnerError if the run fails or
    returns a week missing lunch builds, kid nights or a component slot.
    """
    if week_start.weekday() != _SUNDAY:
        raise ValueError(f"week_start {week_start} is not a Sunday")
    if custody not in get_args(Custody):
        raise ValueError(f"unknown custody {custody!r}")
    if mode not in get_args(PlanMode):
        raise ValueError(f"unknown plan mode {mode!r}")
    pool = _rotation_pool(mealie)
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
    draft = claude_runner.run(prompt, schema=_Draft, timeout=PROPOSE_TIMEOUT_S)
    return WeekProposal(
        week_start=week_start,
        custody=custody,
        mode=mode,
        recipe_options=_favorites(draft.favorites, pool) + draft.new_recipes,
        components=draft.components,
        lunch_builds=draft.lunch_builds,
        kid_nights=draft.kid_nights,
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
    unique = tuple(dict.fromkeys(slugs))
    unknown = [slug for slug in unique if slug not in pool]
    if unknown:
        logger.warning("planner: dropping favorites not in the rotation pool: %s", unknown)
    return tuple(pool[slug] for slug in unique if slug in pool)


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
    return f"- {slug}: {_data(recipe.name)} (hands-on {minutes} min, {batch})"
