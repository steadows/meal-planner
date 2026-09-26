"""The real `MealieClient`: Mealie's REST API over httpx (PLAN.md, Implementation plan).

Endpoints and field names follow Mealie v3.28.0's OpenAPI spec; see
`.brain/research/mealie-v3-api.md`. Only the entry points construct one; every other module takes
a `MealieClient`.
"""

import ipaddress
import itertools
import logging
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
from meals.contracts import TRUSTED, Ingredient, RecipeOption

logger = logging.getLogger(__name__)

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
# RFC 6750 b64token (Mealie's tokens are JWTs). Anything else would reach the HTTP layer, whose
# error for an illegal header value quotes the whole header, token included.
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)
# How Mealie answers a URL it can't scrape: no recipe data, scrape timeout, scraper crash. Mealie
# also answers 500 for a server fault, which it can't be told apart from here, so it is logged.
_SCRAPE_FAILURES = frozenset({400, 408, 500})
# A term of a Mealie duration: "1 hour 30 minutes" (its scraper's format), "25 min", "PT1H30M".
# The number must start the token (not "5" in ".5"); fractions are refused before matching.
_DURATION_PART = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?|\.\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m)(?![a-z])",
    re.IGNORECASE,
)
_UNIT_MINUTES = {"d": 24 * 60, "h": 60, "m": 1}
# A DNS name as sent on the wire (IDNA already applied): anything else, such as an encoded space
# around an IP ("%20127.0.0.1"), must not pass as an ordinary hostname.
_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", re.ASCII)
# Hosts reserved for local networks (RFC 6761, 6762, 8375; ICANN .internal), besides single labels.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


class _Mealie(BaseModel):
    """The subset of a Mealie response this client reads. Mealie adds fields freely; ignore them."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class _Named(_Mealie):
    name: str


class _Referenced(_Mealie):
    name: str | None = None


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
    # An ingredient that is another recipe ("Homemade sauce"); its name isn't in `display`.
    referenced_recipe: _Referenced | None = Field(default=None, alias="referencedRecipe")

    @property
    def text(self) -> str:
        """The line as it reads now. originalText is what was first imported, so it comes last."""
        return (self.display or self.note or self.original_text or "").strip()

    @property
    def structured(self) -> bool:
        """Carries a food or a linked recipe, or an amount of its own (then its note names it)."""
        return (
            self.food is not None
            or self.referenced_recipe is not None
            or bool(self.quantity and self.quantity > 0)
            or bool(self.unit)
        )

    @property
    def item_name(self) -> str | None:
        """What the line is, when Mealie says so: its food, or the recipe it links to."""
        if self.food is not None:
            return self.food.name
        return self.referenced_recipe.name if self.referenced_recipe else None


class _Parsed(_Mealie):
    ingredient: _IngredientLine


class _Step(_Mealie):
    text: str


class _Slugged(_Mealie):
    slug: str


class _Recipe(_Mealie):
    id: UUID
    name: str | None = None
    org_url: str | None = Field(default=None, alias="orgURL")
    recipe_servings: float | None = Field(default=None, alias="recipeServings")
    prep_time: str | None = Field(default=None, alias="prepTime")
    # Not in v3.28.0 (prepTime is free text there); used when a later Mealie provides it.
    prep_time_seconds: int | None = Field(default=None, alias="prepTimeSeconds")
    tags: tuple[_Slugged, ...] | None = None
    recipe_ingredient: tuple[_IngredientLine, ...] = Field(default=(), alias="recipeIngredient")
    recipe_instructions: tuple[_Step, ...] | None = Field(default=None, alias="recipeInstructions")


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
        token = settings.mealie_token.get_secret_value().strip()
        if not _BEARER_TOKEN.fullmatch(token):
            raise RuntimeError("MEALIE_TOKEN is malformed: paste the token from Mealie exactly")
        auth = {"Authorization": f"Bearer {token}"}
        http = httpx.Client(
            base_url=settings.mealie_url,
            headers=auth,
            timeout=MEALIE_TIMEOUT_S,
            transport=transport,
        )
        return cls(http)

    def import_url(self, url: str) -> str:
        try:
            _require_public_url(url)
        except ValueError as exc:
            logger.warning("refused to import %s: %s", _loggable(url), exc)
            raise
        try:
            response = self._http.post(
                "/api/recipes/create/url",
                json={"url": url, "includeTags": False, "includeCategories": False},
                timeout=MEALIE_IMPORT_TIMEOUT_S,
            )
        except httpx.ReadTimeout as exc:
            # Mealie accepted the request and is still scraping; it may finish, and the recipe then
            # appears without a slug here. A connect timeout means Mealie is down, and propagates.
            logger.warning("Mealie timed out importing %s", url)
            raise ValueError(f"Mealie took too long importing {url}") from exc
        if response.status_code in _SCRAPE_FAILURES:
            logger.warning("Mealie couldn't import %s: HTTP %d", url, response.status_code)
            raise ValueError(f"Mealie couldn't import {url} (HTTP {response.status_code})")
        response.raise_for_status()
        slug = _SLUG_RESPONSE.validate_json(response.content)
        logger.info("imported %s as %s", url, slug)
        return slug

    def get_recipe(self, slug: str) -> RecipeOption:
        recipe = self._fetch_recipe(slug)
        seconds = recipe.prep_time_seconds
        option = RecipeOption(
            name=recipe.name or slug,
            url=recipe.org_url or "",
            source=_source(recipe.org_url),
            hands_on_min=_minutes(recipe.prep_time) if seconds is None else seconds // 60,
            servings=_servings(recipe),
            batch_ok=any(tag.slug == BATCH_OK_TAG for tag in recipe.tags or ()),
            fit_note="",
            ingredients=self._ingredients(recipe.recipe_ingredient),
            steps=tuple(
                step.text for step in recipe.recipe_instructions or () if step.text.strip()
            ),
        )
        # The scraped fields above go through the untrusted path; only the slug is added under
        # contracts.TRUSTED. Revalidated rather than model_copy'd, which would skip its checks.
        fields = option.model_dump() | {"mealie_slug": slug}
        return RecipeOption.model_validate(fields, context={TRUSTED: True})

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        if not _SLUG.fullmatch(tag):
            return ()
        # Mealie's by-slug lookup answers 500 for an unknown slug, so find the tag in the list.
        tags = map(_Tag.model_validate, self._paged("/api/organizers/tags", {}))
        tag_id = next((t.id for t in tags if t.slug == tag), None)
        if tag_id is None:
            return ()
        # Filter by the resolved id, so an unknown tag can never read as "no filter".
        items = self._paged(
            "/api/recipes", {"tags": tag_id, "orderBy": "slug", "orderDirection": "asc"}
        )
        return tuple(_Slugged.model_validate(item).slug for item in items)

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        """Replace this client's entries on the cook day (`week_start`) with `slugs`, one dinner
        entry each. Every slug is resolved before anything is written.

        Not safe to run concurrently for one week: two overlapping calls can leave the union of
        their recipes. Callers serialize: the runtime model (ADR-0001) has only `reconcile`
        publish, under its job lock. A retried publish is safe, since reruns replace rather than
        append."""
        wanted = tuple(self._fetch_recipe(slug).id for slug in dict.fromkeys(slugs))
        day = week_start.isoformat()
        entries = self._paged("/api/households/mealplans", {"start_date": day, "end_date": day})
        ours = [e for e in map(_PlanEntry.model_validate, entries) if e.text == PLAN_ENTRY_MARKER]
        on_plan: set[UUID | None] = set()
        for entry in ours:
            if entry.recipe_id in wanted and entry.recipe_id not in on_plan:
                on_plan.add(entry.recipe_id)
            else:  # no longer planned, or a duplicate of one kept above
                self._http.delete(f"/api/households/mealplans/{entry.id}").raise_for_status()
        added = [recipe_id for recipe_id in wanted if recipe_id not in on_plan]
        for recipe_id in added:
            self._create_entry(day, recipe_id)
        logger.info(
            "meal plan %s: %d kept, %d removed, %d added",
            day,
            len(on_plan),
            len(ours) - len(on_plan),
            len(added),
        )
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
        Mealie's parser in one call. A line the parser can't read keeps its raw text."""
        kept = [line for line in lines if line.structured or line.text]
        texts = list(dict.fromkeys(line.text for line in kept if not line.structured))
        parsed = dict(zip(texts, self._parse(texts), strict=True))
        return tuple(
            _to_ingredient(line if line.structured else parsed[line.text], fallback=line.text)
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
        raise ValueError("not a fetchable URL") from exc
    host = parsed.host.rstrip(".").lower()
    if parsed.scheme not in {"http", "https"} or not host or parsed.userinfo:
        raise ValueError("only public http(s) URLs without credentials can be imported")
    ip = _ip_literal(host)
    if ip is None and not _HOSTNAME.fullmatch(
        parsed.raw_host.decode("ascii", "replace").rstrip(".").lower()
    ):
        raise ValueError("refusing a malformed host")
    if ip is not None:
        # is_global is True for most multicast and for some reserved IPv6 (::7f00:1, NAT64).
        local = ip.is_multicast or ip.is_reserved or not ip.is_global
    else:
        local = "." not in host or host.endswith(_LOCAL_SUFFIXES)  # single-label covers "localhost"
    if local:
        raise ValueError("refusing a local or private address")


def _loggable(url: str) -> str:
    """The URL without any user:password part, for logs. Guard errors never quote the URL."""
    try:
        return str(httpx.URL(url).copy_with(userinfo=b""))
    except httpx.InvalidURL:
        return "<unparseable URL>"


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
    qty = line.quantity if line.quantity and line.quantity > 0 else None
    unit = line.unit.name if line.unit else None
    if line.item_name:
        return Ingredient(name=line.item_name, qty=qty, unit=unit, note=line.note or "")
    if qty is None and unit is None:
        return Ingredient(name=fallback)
    return Ingredient(name=(line.note or "").strip() or fallback, qty=qty, unit=unit)


def _minutes(text: str | None) -> int | None:
    """Minutes in a Mealie duration string; a bare number is minutes. None if unreadable,
    including any fraction ("1/2 hour"): summing the parts around it would be wrong."""
    if not text or "/" in text:
        return None
    parts = _DURATION_PART.findall(text)
    if parts:
        return round(sum(float(n) * _UNIT_MINUTES[unit[0].lower()] for n, unit in parts))
    stripped = text.strip()
    return int(stripped) if stripped.isdigit() else None


def _source(org_url: str | None) -> str:
    """The site a recipe came from. orgURL is free text in Mealie, so a malformed one reads as
    unknown rather than failing the whole recipe."""
    try:
        host = urlsplit(org_url).hostname if org_url else None
    except ValueError:
        host = None
    return host.removeprefix("www.") if host else "mealie"


def _servings(recipe: _Recipe) -> int | None:
    """recipeServings only: recipeYieldQuantity counts cookies or loaves, not people."""
    value = recipe.recipe_servings
    return max(1, round(value)) if value and value > 0 else None
