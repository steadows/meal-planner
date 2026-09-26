"""The Saturday planner: recent weeks + profile + pantry + the Mealie rotation pool give next week's
WeekProposal, in one web-enabled `claude -p` run (PLAN.md → Phase 5, Food system).

Claude chooses the food. Everything that doesn't need judgment is set in code: the week, custody
and mode come from the caller, favorites resolve to the exact Mealie recipes, and the pantry
questions are the most overdue staples.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date

from pydantic import Field

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
_SUNDAY = 6


class _Draft(Contract):
    """What Claude decides. Everything else in the WeekProposal is set by `propose`."""

    favorites: tuple[str, ...] = Field(description="Slugs copied exactly from the rotation pool")
    new_recipes: tuple[RecipeOption, ...] = Field(description="Fresh finds from the web")
    components: Components
    lunch_builds: tuple[str, ...] = Field(description="The two lunch build names picked")
    kid_nights: tuple[str, ...] = Field(description="One line per night Miles is here")


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

    `recent` is the last few weeks actually cooked, newest first. Raises ValueError if
    `week_start` isn't a Sunday (before any Claude run), and ClaudeRunnerError if the run fails.
    """
    if week_start.weekday() != _SUNDAY:
        raise ValueError(f"week_start {week_start} is not a Sunday")
    pool = {slug: mealie.get_recipe(slug) for slug in mealie.list_by_tag(ROTATION_TAG)}
    prompt = claude_runner.load_prompt(
        "planner",
        "propose",
        week_start=week_start.isoformat(),
        custody=custody,
        mode=mode,
        prefs=get_settings().prefs_file.read_text(encoding="utf-8"),
        recent=_recent_digest(recent),
        pool=_pool_digest(pool),
    )
    draft = claude_runner.run(prompt, schema=_Draft)
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


def _favorites(slugs: Sequence[str], pool: Mapping[str, RecipeOption]) -> tuple[RecipeOption, ...]:
    unknown = [slug for slug in slugs if slug not in pool]
    if unknown:
        logger.warning("planner: dropping favorites not in the rotation pool: %s", unknown)
    return tuple(pool[slug] for slug in slugs if slug in pool)


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
    parts = "; ".join(f"{slot}: {', '.join(names)}" for slot, names in slots.items() if names)
    return f"- {week.week_start.isoformat()} ({week.mode}): {parts}"


def _pool_digest(pool: Mapping[str, RecipeOption]) -> str:
    if not pool:
        return "(empty: find every recipe on the web)"
    return "\n".join(
        f"- {slug}: {recipe.name} (hands-on {recipe.hands_on_min or '?'} min, "
        f"{'batches well' if recipe.batch_ok else 'does not batch'})"
        for slug, recipe in pool.items()
    )
