"""The real `MealieClient`: Mealie's REST API over httpx (PLAN.md, Implementation plan).

Endpoints and field names follow Mealie v3.28.0's OpenAPI spec; see
`.brain/research/mealie-v3-api.md`. Only the entry points construct one; every other module takes
a `MealieClient`.
"""

import ipaddress
import itertools
import re
import socket
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from typing import Any, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from meals.config import Settings, get_settings
from meals.contracts import Ingredient, RecipeOption

BATCH_OK_TAG = "batch-ok"
# Written into every meal-plan entry this client creates, so set_meal_plan replaces its own
# entries and never touches the ones Steve added by hand.
PLAN_ENTRY_MARKER = "Planned by meal-planner"
PLAN_ENTRY_TYPE = "dinner"
PAGE_SIZE = 100
MEALIE_TIMEOUT_S = 10.0
# A scrape can be slow, and timing out early would orphan a recipe Mealie still finishes importing.
MEALIE_IMPORT_TIMEOUT_S = 60.0
# What Mealie's slugify produces. Slugs and tags go into URL paths, and some come from Claude's
# output, so anything else is treated as unknown and never sent.
_SLUG = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
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
    total_pages: int
    items: tuple[dict[str, Any], ...]


_SLUG_RESPONSE = TypeAdapter(str)


class HttpMealieClient:
    """`MealieClient` over an `httpx.Client` that already carries the base URL, auth and timeout."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None
    ) -> Self:
        """The client the entry points use, from `.env`. `transport` is for tests."""
        settings = get_settings() if settings is None else settings
        if settings.mealie_token is None:
            raise RuntimeError("MEALIE_TOKEN is not set: add a Mealie API token to .env")
        auth = {"Authorization": f"Bearer {settings.mealie_token.get_secret_value()}"}
        http = httpx.Client(
            base_url=settings.mealie_url,
            headers=auth,
            timeout=MEALIE_TIMEOUT_S,
            transport=transport,
        )
        return cls(http)

    def import_url(self, url: str) -> str:
        _require_public_url(url)
        response = self._http.post(
            "/api/recipes/create/url",
            json={"url": url, "includeTags": False, "includeCategories": False},
            timeout=MEALIE_IMPORT_TIMEOUT_S,
        )
        if response.status_code in _SCRAPE_FAILURES:
            raise ValueError(f"Mealie couldn't import {url} (HTTP {response.status_code})")
        response.raise_for_status()
        return _SLUG_RESPONSE.validate_json(response.content)

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
        if not _SLUG.fullmatch(tag):
            return ()
        response = self._http.get(f"/api/organizers/tags/slug/{tag}")
        if response.status_code == 404:
            return ()
        response.raise_for_status()
        # Filter by the resolved id, so an unknown tag can never read as "no filter".
        tag_id = _Tag.model_validate(response.json()).id
        items = self._paged(
            "/api/recipes", {"tags": tag_id, "orderBy": "slug", "orderDirection": "asc"}
        )
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
        if not _SLUG.fullmatch(slug):
            raise KeyError(slug)
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
        for page in itertools.count(1):
            response = self._http.get(path, params={**params, "page": page, "perPage": PAGE_SIZE})
            response.raise_for_status()
            body = _Page.model_validate(response.json())
            yield from body.items
            if page >= body.total_pages:
                return


def _require_public_url(url: str) -> None:
    """Refuse a URL Mealie shouldn't fetch from inside the home network (SSRF; see
    connections/mealie-search-shared-rule). Mealie's own transport is the primary control: it
    resolves the host, blocks the same ranges and pins the connection. This is defence in depth,
    with no DNS lookup, judging the host exactly as httpx (the fetcher) parses it."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise ValueError(f"not a fetchable URL: {url!r}") from exc
    host = parsed.host.rstrip(".").lower()
    if parsed.scheme not in {"http", "https"} or not host or parsed.userinfo:
        raise ValueError(f"only public http(s) URLs can be imported: {url!r}")
    ip = _ip_literal(host)
    if ip is not None:
        local = ip.is_multicast or not ip.is_global  # is_global is True for most multicast
    else:
        local = "." not in host or host.endswith(".localhost")  # single-label covers "localhost"
    if local:
        raise ValueError(f"refusing a local or private address: {url!r}")


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a host names without DNS, including legacy forms (`0x7f.1`), IPv4-mapped
    IPv6 unwrapped; None for a hostname."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    # Recent CPython patch releases already judge ::ffff:a.b.c.d by its IPv4 address; older 3.11
    # releases (still allowed by requires-python) don't, so unwrap explicitly.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


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
