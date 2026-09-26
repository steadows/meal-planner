"""Lane C against a real Mealie (PLAN.md, Concurrency lanes: imports a real URL and writes a plan).

Needs a running Mealie v3.28.0+ with MEALIE_URL and MEALIE_TOKEN set; run with -m integration.
Every write is undone in teardown.
"""

import os
from collections.abc import Iterator
from datetime import date

import httpx
import pytest

from meals.config import Settings, get_settings
from meals.mealie_client import PLAN_ENTRY_MARKER, HttpMealieClient

pytestmark = pytest.mark.integration

RECIPE_URL = os.environ.get("MEALIE_IT_RECIPE_URL", "https://www.budgetbytes.com/basic-chili/")
PLAN_DAY = date(2099, 1, 4)  # far enough out that a test entry never lands on a real week


@pytest.fixture(scope="module")
def settings() -> Settings:
    settings = get_settings()
    if settings.mealie_token is None:
        pytest.skip("MEALIE_TOKEN is not set; these tests need a running Mealie")
    return settings


@pytest.fixture(scope="module")
def mealie(settings: Settings) -> HttpMealieClient:
    return HttpMealieClient.from_settings(settings)


@pytest.fixture(scope="module")
def raw_http(settings: Settings) -> Iterator[httpx.Client]:
    """Plain authed httpx, for the cleanup and checks the MealieClient Protocol doesn't offer."""
    assert settings.mealie_token is not None
    auth = {"Authorization": f"Bearer {settings.mealie_token.get_secret_value()}"}
    with httpx.Client(base_url=settings.mealie_url, headers=auth) as http:
        yield http


@pytest.fixture(scope="module")
def imported(mealie: HttpMealieClient, raw_http: httpx.Client) -> Iterator[str]:
    """A freshly imported recipe, deleted afterwards. A slug that already existed is never deleted.

    If the client times out while Mealie finishes the scrape, the recipe is orphaned: teardown
    can't know its slug.
    """
    existing = recipe_slugs(raw_http)
    slug = mealie.import_url(RECIPE_URL)
    if slug in existing:
        pytest.fail(f"import_url returned {slug!r}, which was already in Mealie; not deleting it")
    yield slug
    raw_http.delete(f"/api/recipes/{slug}").raise_for_status()


def recipe_slugs(raw_http: httpx.Client) -> set[str]:
    slugs: set[str] = set()
    page = 1
    while True:
        response = raw_http.get("/api/recipes", params={"page": page, "perPage": 100})
        response.raise_for_status()
        body = response.json()
        slugs.update(item["slug"] for item in body["items"])
        if page >= body["total_pages"]:
            return slugs
        page += 1


def our_entry_ids(raw_http: httpx.Client) -> list[int]:
    day = PLAN_DAY.isoformat()
    response = raw_http.get(
        "/api/households/mealplans",
        params={"start_date": day, "end_date": day, "page": 1, "perPage": 100},
    )
    response.raise_for_status()
    return sorted(e["id"] for e in response.json()["items"] if e["text"] == PLAN_ENTRY_MARKER)


def test_an_imported_recipe_comes_back_with_parsed_ingredients(
    mealie: HttpMealieClient, imported: str
) -> None:
    recipe = mealie.get_recipe(imported)

    assert recipe.mealie_slug == imported
    assert recipe.name
    assert recipe.ingredients
    assert any(ingredient.qty is not None for ingredient in recipe.ingredients)


def test_get_recipe_raises_key_error_for_an_unknown_slug(mealie: HttpMealieClient) -> None:
    with pytest.raises(KeyError):
        mealie.get_recipe("definitely-not-a-recipe-zz9")


def test_list_by_tag_returns_empty_for_an_unknown_tag(mealie: HttpMealieClient) -> None:
    assert mealie.list_by_tag("no-such-tag-zz9") == ()


def test_set_meal_plan_writes_once_then_clears(
    mealie: HttpMealieClient, raw_http: httpx.Client, imported: str
) -> None:
    try:
        mealie.set_meal_plan(PLAN_DAY, [imported])
        first = our_entry_ids(raw_http)
        mealie.set_meal_plan(PLAN_DAY, [imported])

        assert len(first) == 1
        assert our_entry_ids(raw_http) == first

        mealie.set_meal_plan(PLAN_DAY, [])
        assert our_entry_ids(raw_http) == []
    finally:
        for entry_id in our_entry_ids(raw_http):
            raw_http.delete(f"/api/households/mealplans/{entry_id}").raise_for_status()
