"""Shared contracts between meals modules (PLAN.md, Shared contracts).

Only the contracts lane edits this file. Other lanes request changes with a small PR.
"""

import re
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema

PlanMode = Literal["mix", "recipes", "components"]
Custody = Literal["wed+fri_sat", "wed+sat_sun"]
IntentKind = Literal[
    "pick", "swap", "custody", "pantry_flip", "add_item", "find", "save", "rate", "mode"
]
PantryCategory = Literal["staple", "perishable", "fallback"]
PantryStatus = Literal["have", "buy_next_time"]

MAX_PANTRY_QUESTIONS = 3

# Validation-context key marking a payload as Claude's output (validate_claude_output). Fields only
# trusted code may set refuse a value under it.
UNTRUSTED = "untrusted"

M = TypeVar("M", bound=BaseModel)


# Matched against the raw string, not a parsed URL: Python's urlsplit and a browser disagree on
# backslashes, userinfo and whitespace, and that gap would let another host through. Printable
# ASCII only, case-folded as ASCII: Unicode case folding lets "ı" match "i", and a browser sends
# "meıjer.com" to a different (punycode) host.
_MEIJER_URL = re.compile(
    r"\Ahttps://(?:[a-z0-9-]+\.)*meijer\.com(?::443)?(?:[/?#][!-\[\]-~]*)?\Z",
    re.ASCII | re.IGNORECASE,
)


def _require_meijer_url(url: str) -> str:
    """The logged-in cart session may only open meijer.com (CLAUDE.md; PLAN.md, Risks)."""
    if not _MEIJER_URL.match(url):
        raise ValueError("must be an https URL on meijer.com")
    return url


MeijerUrl = Annotated[str, AfterValidator(_require_meijer_url)]

# Spelled out rather than strftime("%A"), which follows the process locale.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _require_sunday(day: date) -> date:
    """Sunday is the only cook (PLAN.md, Constraints), so a week is named by its Sunday."""
    if day.weekday() != 6:
        raise ValueError(
            f"{day.isoformat()} is a {_WEEKDAYS[day.weekday()]}; "
            "week_start must be the Sunday of the cook"
        )
    return day


SundayDate = Annotated[date, AfterValidator(_require_sunday)]

# The same allowlist the Mealie client enforces before a slug reaches a URL path. Pydantic's
# regex engine anchors `$` at the very end, so "slug\n" doesn't match.
MealieSlug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]


class Contract(BaseModel):
    """Frozen, and rejects unknown fields so Claude's structured output can't smuggle extras."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Ingredient(Contract):
    name: str
    qty: float | None = None
    unit: str | None = None
    note: str = ""


class RecipeOption(Contract):
    name: str
    url: str
    source: str = Field(description="Site name, e.g. 'budgetbytes.com'")
    hands_on_min: int | None = Field(description="Active prep minutes; null if unknown")
    servings: int | None
    batch_ok: bool = Field(description="Batches and reheats well")
    fit_note: str = Field(description="One line on fit against Steve's preferences profile")
    ingredients: tuple[Ingredient, ...]
    steps: tuple[str, ...]
    # Hidden from Claude's schema; only the Mealie client sets it (get_recipe), so a pick can go on
    # the meal plan without re-importing, which would duplicate the recipe in Mealie.
    mealie_slug: SkipJsonSchema[MealieSlug | None] = None

    @field_validator("mealie_slug")
    @classmethod
    def _trusted_mealie_slug(cls, slug: str | None, info: ValidationInfo) -> str | None:
        if slug is not None and (info.context or {}).get(UNTRUSTED):
            # An injected page could otherwise point a web find at an existing Mealie recipe.
            raise ValueError("mealie_slug is set by the Mealie client, never by Claude")
        return slug


class Components(Contract):
    """The Sunday-cook building blocks, by slot (PLAN.md, Food system)."""

    proteins: tuple[str, ...] = ()
    grains: tuple[str, ...] = ()
    veg: tuple[str, ...] = ()
    sauces: tuple[str, ...] = ()
    fresh: tuple[str, ...] = ()


class WeekProposal(Contract):
    week_start: SundayDate = Field(description="The Sunday of the cook")
    custody: Custody
    mode: PlanMode = "mix"
    recipe_options: tuple[RecipeOption, ...]
    components: Components
    lunch_builds: tuple[str, ...] = Field(description="Names of Steve's lunch builds for the week")
    kid_nights: tuple[str, ...] = Field(
        description="One line per kid night, e.g. 'Wed cook-with-Miles (ravioli)'"
    )
    pantry_questions: tuple[str, ...] = Field(
        max_length=MAX_PANTRY_QUESTIONS,
        description="Pantry item names to ask about, e.g. 'olive oil' (item names, not question text)",
    )


class Intent(Contract):
    kind: IntentKind
    args: dict[str, str] = Field(description="Kind-specific arguments, e.g. {'item': 'eggs'}")


class CartItem(Contract):
    name: str
    qty: float = Field(gt=0)
    unit: str | None = None
    meijer_url: MeijerUrl | None = None
    preferred_name: str | None = None
    substitute_ok: bool = True


class CartList(Contract):
    week_start: SundayDate
    items: tuple[CartItem, ...]


class Substitution(Contract):
    wanted: str
    used: str


class CartReport(Contract):
    added: tuple[str, ...]
    substituted: tuple[Substitution, ...]
    missing: tuple[str, ...]
    subtotal_cents: int = Field(ge=0)


class PantryItem(Contract):
    """The domain fields of a `pantry_item` row (PLAN.md, Table schema).

    Not a raw row: `aliases` is the decoded JSON array, and `updated_at` is bookkeeping the
    contract leaves out. The pantry lane maps rows to this model.
    """

    id: int
    name: str
    aliases: tuple[str, ...] = ()
    category: PantryCategory
    status: PantryStatus = "have"
    typical_interval_days: int | None = Field(default=None, gt=0)
    last_purchased: date | None = None
    next_ask_on: date | None = None
    default_qty: float | None = None
    default_unit: str | None = None
    meijer_product_id: str | None = None
    meijer_url: MeijerUrl | None = None
    preferred_product_name: str | None = None
    substitute_ok: bool = True
    for_miles: bool = False
    notes: str | None = None


class ClaudeRunnerError(Exception):
    """A `claude -p` run failed: bad exit, timeout, error result, or output that isn't valid."""

    def __init__(self, reason: str, raw_output: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw_output = raw_output


def validate_claude_output(schema: type[M], payload: object) -> M:
    """Validate Claude's structured output. Both runners use it, and so must any caller that
    validates a schemaless `run()` result itself: it refuses fields only trusted code may set."""
    return schema.model_validate(payload, context={UNTRUSTED: True})


@runtime_checkable
class MealieClient(Protocol):
    def import_url(self, url: str) -> str:
        """Import a recipe by URL and return its slug. Raises ValueError if Mealie can't scrape it."""
        ...

    def get_recipe(self, slug: str) -> RecipeOption:
        """The recipe, with `mealie_slug` set to `slug`. Raises KeyError for an unknown slug."""
        ...

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        """Slugs carrying the tag; empty for an unknown tag."""
        ...

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        """Put the recipes on the week's plan and return the plan ref. Raises KeyError for an unknown slug."""
        ...


@runtime_checkable
class Pantry(Protocol):
    """The pantry rules (PLAN.md, Pantry rules).

    An item's *ask date* is `next_ask_on` when set; otherwise `last_purchased` plus
    ceil(9 * interval / 10) days (PLAN: ask at 90%) when both are known; otherwise it has none.

    Item names are unique under casefold, which is stricter than the schema's ASCII-only
    `COLLATE NOCASE`, so implementations reject a duplicate on insert. Every method taking a
    `name` matches it against item names and aliases after `strip().casefold()`, and an exact
    name beats another item's alias. An unknown name returns None and writes nothing.
    """

    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        """Staples flagged `buy_next_time`, or whose ask date is on or before `on`. Flagged first
        (those with no ask date last among them), then most days past the ask date, then name.
        Capping how many to ask is the caller's job."""
        ...

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        """Set the item's status. Returns the updated item."""
        ...

    def log_purchase(
        self, name: str, on: date, qty: float | None = None, price_cents: int | None = None
    ) -> PantryItem | None:
        """Record a purchase. One purchase per item per day: a repeat for the same day is a replay
        and writes nothing. A purchase on or after `last_purchased` makes `on` the last purchase,
        sets status `have` and clears `next_ask_on`; an earlier one only adds history. The
        implementation may re-learn the interval. Returns the updated item.

        Raises ValueError, before looking the name up, if `qty` is given and isn't a finite
        number above 0, or `price_cents` is negative."""
        ...

    def get_item(self, name: str) -> PantryItem | None:
        """The item with this name or alias."""
        ...

    def list_items(self) -> tuple[PantryItem, ...]:
        """Every item, in `id` order."""
        ...
