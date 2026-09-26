"""Shared contracts between meals modules (PLAN.md, Shared contracts).

Only the contracts lane edits this file. Other lanes request changes with a small PR.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

PlanMode = Literal["mix", "recipes", "components"]
Custody = Literal["wed+fri_sat", "wed+sat_sun"]
IntentKind = Literal[
    "pick", "swap", "custody", "pantry_flip", "add_item", "find", "save", "rate", "mode"
]
PantryCategory = Literal["staple", "perishable", "fallback"]
PantryStatus = Literal["have", "buy_next_time"]

MAX_PANTRY_QUESTIONS = 3


def _require_meijer_url(url: str) -> str:
    """The logged-in cart session may only open meijer.com (CLAUDE.md; PLAN.md, Risks)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "meijer.com" or host.endswith(".meijer.com")):
        raise ValueError("must be an https URL on meijer.com")
    return url


MeijerUrl = Annotated[str, AfterValidator(_require_meijer_url)]


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


class Components(Contract):
    """The Sunday-cook building blocks, by slot (PLAN.md, Food system)."""

    proteins: tuple[str, ...] = ()
    grains: tuple[str, ...] = ()
    veg: tuple[str, ...] = ()
    sauces: tuple[str, ...] = ()
    fresh: tuple[str, ...] = ()


class WeekProposal(Contract):
    week_start: date = Field(description="The Sunday of the cook")
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
    week_start: date
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


@runtime_checkable
class MealieClient(Protocol):
    def import_url(self, url: str) -> str:
        """Import a recipe by URL and return its slug. Raises ValueError if Mealie can't scrape it."""
        ...

    def get_recipe(self, slug: str) -> RecipeOption:
        """Raises KeyError for an unknown slug."""
        ...

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        """Slugs carrying the tag; empty for an unknown tag."""
        ...

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        """Put the recipes on the week's plan and return the plan ref. Raises KeyError for an unknown slug."""
        ...


@runtime_checkable
class Pantry(Protocol):
    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        """Staples due on `on`, most overdue first: flagged `buy_next_time`, or at least 90% of
        their interval since last purchase (PLAN.md). Capping how many to ask is the caller's job."""
        ...

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        """Flip an item by name or alias, case-insensitively. Returns the updated item, or None
        if the item is unknown."""
        ...
