"""The real `MealieClient`: Mealie's REST API over httpx (PLAN.md, Implementation plan).

Endpoints and field names follow Mealie v3.28.0's OpenAPI spec; see
`.brain/research/mealie-v3-api.md`. Only the entry points construct one; every other module takes
a `MealieClient`.
"""

from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from meals.contracts import Ingredient, RecipeOption

BATCH_OK_TAG = "batch-ok"
# Written into every meal-plan entry this client creates, so set_meal_plan replaces its own
# entries and never touches the ones Steve added by hand.
PLAN_ENTRY_MARKER = "Planned by meal-planner"
PLAN_ENTRY_TYPE = "dinner"
PAGE_SIZE = 100
# How Mealie answers a URL it can't scrape: no recipe data, timeout, bad URL, scraper crash.
_SCRAPE_FAILURES = frozenset({400, 408, 422, 500})


class _Mealie(BaseModel):
    """The subset of a Mealie response this client reads. Mealie adds fields freely; ignore them."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class _Named(_Mealie):
    name: str


class _Tag(_Mealie):
    id: str
    slug: str


class _IngredientLine(_Mealie):
    food: _Named | None = None
    unit: _Named | None = None
    quantity: float | None = None
    note: str | None = None
    display: str | None = None
    original_text: str | None = Field(default=None, alias="originalText")

    @property
    def text(self) -> str:
        return (self.original_text or self.display or self.note or "").strip()


class _Parsed(_Mealie):
    ingredient: _IngredientLine


class _Step(_Mealie):
    text: str


class _Recipe(_Mealie):
    id: UUID
    name: str | None = None
    org_url: str | None = Field(default=None, alias="orgURL")
    recipe_servings: float | None = Field(default=None, alias="recipeServings")
    recipe_yield_quantity: float | None = Field(default=None, alias="recipeYieldQuantity")
    prep_time_seconds: int | None = Field(default=None, alias="prepTimeSeconds")
    tags: tuple[_Tag, ...] | None = None
    recipe_ingredient: tuple[_IngredientLine, ...] = Field(default=(), alias="recipeIngredient")
    recipe_instructions: tuple[_Step, ...] | None = Field(default=None, alias="recipeInstructions")


class _Slugged(_Mealie):
    slug: str


class _PlanEntry(_Mealie):
    id: int
    text: str = ""
    recipe_id: UUID | None = Field(default=None, alias="recipeId")


class _Page(_Mealie):
    page: int
    total_pages: int
    items: tuple[dict[str, Any], ...]


class HttpMealieClient:
    """`MealieClient` over an `httpx.Client` that already carries the base URL, auth and timeout."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def import_url(self, url: str) -> str:
        response = self._http.post(
            "/api/recipes/create/url",
            json={"url": url, "includeTags": False, "includeCategories": False},
        )
        if response.status_code in _SCRAPE_FAILURES:
            raise ValueError(f"Mealie couldn't import {url} (HTTP {response.status_code})")
        response.raise_for_status()
        slug = response.json()
        if not isinstance(slug, str):
            raise TypeError(f"Mealie returned {type(slug).__name__} as the new recipe's slug")
        return slug

    def get_recipe(self, slug: str) -> RecipeOption:
        recipe = self._fetch_recipe(slug)
        prep = recipe.prep_time_seconds
        return RecipeOption(
            name=recipe.name or slug,
            url=recipe.org_url or "",
            source=_source(recipe.org_url),
            hands_on_min=None if prep is None else prep // 60,
            servings=_servings(recipe),
            batch_ok=any(tag.slug == BATCH_OK_TAG for tag in recipe.tags or ()),
            fit_note="",
            ingredients=self._ingredients(recipe.recipe_ingredient),
            steps=tuple(
                step.text for step in recipe.recipe_instructions or () if step.text.strip()
            ),
        )

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        response = self._http.get(f"/api/organizers/tags/slug/{tag}")
        if response.status_code == 404:
            return ()
        response.raise_for_status()
        # Filter by the resolved id, so an unknown tag can never read as "no filter".
        params = {"tags": _Tag.model_validate(response.json()).id, "orderBy": "slug"}
        items = self._paged("/api/recipes", params | {"orderDirection": "asc"})
        return tuple(_Slugged.model_validate(item).slug for item in items)

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        """Replace this client's entries on the cook day (`week_start`) with `slugs`, one dinner
        entry each. Every slug is resolved before anything is written."""
        wanted = tuple(self._fetch_recipe(slug).id for slug in dict.fromkeys(slugs))
        day = week_start.isoformat()
        entries = self._paged("/api/households/mealplans", {"start_date": day, "end_date": day})
        ours = [e for e in map(_PlanEntry.model_validate, entries) if e.text == PLAN_ENTRY_MARKER]
        for entry in ours:
            if entry.recipe_id not in wanted:
                self._http.delete(f"/api/households/mealplans/{entry.id}").raise_for_status()
        on_plan = {entry.recipe_id for entry in ours}
        for recipe_id in wanted:
            if recipe_id not in on_plan:
                self._create_entry(day, recipe_id)
        return day

    def _fetch_recipe(self, slug: str) -> _Recipe:
        response = self._http.get(f"/api/recipes/{slug}")
        if response.status_code == 404:
            raise KeyError(slug)
        response.raise_for_status()
        return _Recipe.model_validate(response.json())

    def _create_entry(self, day: str, recipe_id: UUID) -> None:
        entry = {
            "date": day,
            "entryType": PLAN_ENTRY_TYPE,
            "title": "",
            "text": PLAN_ENTRY_MARKER,
            "recipeId": str(recipe_id),
        }
        self._http.post("/api/households/mealplans", json=entry).raise_for_status()

    def _ingredients(self, lines: Sequence[_IngredientLine]) -> tuple[Ingredient, ...]:
        """Structured lines as they are; text-only lines (what a URL import leaves) through
        Mealie's parser in one call. A line the parser can't find a food in keeps its raw text."""
        kept = [line for line in lines if line.food is not None or line.text]
        texts = list(dict.fromkeys(line.text for line in kept if line.food is None))
        parsed = dict(zip(texts, self._parse(texts), strict=True))
        return tuple(
            _to_ingredient(line if line.food else parsed[line.text], fallback=line.text)
            for line in kept
        )

    def _parse(self, texts: list[str]) -> tuple[_IngredientLine, ...]:
        if not texts:
            return ()
        response = self._http.post(
            "/api/parser/ingredients", json={"parser": "nlp", "ingredients": texts}
        )
        response.raise_for_status()
        return tuple(_Parsed.model_validate(item).ingredient for item in response.json())

    def _paged(self, path: str, params: Mapping[str, str]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            response = self._http.get(path, params={**params, "page": page, "perPage": PAGE_SIZE})
            response.raise_for_status()
            body = _Page.model_validate(response.json())
            yield from body.items
            if page >= body.total_pages:
                return
            page += 1


def _to_ingredient(line: _IngredientLine, fallback: str) -> Ingredient:
    if line.food is None:
        return Ingredient(name=fallback)
    qty = line.quantity if line.quantity and line.quantity > 0 else None
    unit = line.unit.name if line.unit else None
    return Ingredient(name=line.food.name, qty=qty, unit=unit, note=line.note or "")


def _source(org_url: str | None) -> str:
    host = urlsplit(org_url).hostname if org_url else None
    return host.removeprefix("www.") if host else "mealie"


def _servings(recipe: _Recipe) -> int | None:
    for value in (recipe.recipe_servings, recipe.recipe_yield_quantity):
        if value and value > 0:
            return max(1, round(value))
    return None
