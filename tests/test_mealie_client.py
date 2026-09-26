import json
import uuid
from datetime import date
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from meals.contracts import Ingredient, MealieClient
from meals.mealie_client import BATCH_OK_TAG, PLAN_ENTRY_MARKER, HttpMealieClient

WEEK = date(2026, 9, 27)
Json = dict[str, Any]


def recipe_json(slug: str, **fields: Any) -> Json:
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, slug)),
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "orgURL": f"https://www.budgetbytes.com/{slug}/",
        "recipeServings": 4.0,
        "recipeYieldQuantity": 0.0,
        "prepTimeSeconds": 1200,
        "tags": [],
        "recipeIngredient": [],
        "recipeInstructions": [{"text": "Cook it."}],
    } | fields


def structured(food: str, qty: float, unit: str | None = None, note: str = "") -> Json:
    return {
        "food": {"id": f"food-{food}", "name": food},
        "quantity": qty,
        "unit": {"id": f"unit-{unit}", "name": unit} if unit else None,
        "note": note,
        "display": f"{qty} {unit or ''} {food}",
        "originalText": None,
    }


def raw(text: str) -> Json:
    """A line as a plain URL import leaves it: text only, Mealie's default quantity of 0."""
    return {
        "food": None,
        "unit": None,
        "quantity": 0,
        "note": "",
        "display": text,
        "originalText": text,
    }


class MealieStub:
    """Just enough of Mealie's API for httpx.MockTransport, recording every request."""

    def __init__(self) -> None:
        self.recipes: dict[str, Json] = {}
        self.tags: dict[str, str] = {}  # tag slug -> tag id
        self.tagged: dict[str, list[str]] = {}  # tag id -> recipe slugs
        self.entries: dict[int, Json] = {}
        self.parses: dict[str, Json] = {}  # ingredient text -> parsed RecipeIngredient
        self.import_status = 201
        self.create_entry_status = 201
        self.requests: list[httpx.Request] = []

    def add_entry(self, recipe_slug: str, text: str, on: date = WEEK) -> int:
        entry_id = len(self.entries) + 1
        self.entries[entry_id] = {
            "id": entry_id,
            "date": on.isoformat(),
            "entryType": "dinner",
            "title": "",
            "text": text,
            "recipeId": self.recipes[recipe_slug]["id"],
        }
        return entry_id

    def writes(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests if r.method in {"POST", "DELETE"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        params = parse_qs(request.url.query.decode())
        body: Any = json.loads(request.content) if request.content else None

        if (method, path) == ("POST", "/api/recipes/create/url"):
            if self.import_status != 201:
                return httpx.Response(self.import_status, json={"detail": "scrape failed"})
            return httpx.Response(201, json="imported-recipe")
        if method == "GET" and path.startswith("/api/organizers/tags/slug/"):
            tag = path.rsplit("/", 1)[1]
            if tag not in self.tags:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json={"id": self.tags[tag], "name": tag, "slug": tag})
        if (method, path) == ("GET", "/api/recipes"):
            slugs = sorted(self.tagged.get(params["tags"][0], []))
            per_page, page = int(params["perPage"][0]), int(params["page"][0])
            chunk = slugs[(page - 1) * per_page : page * per_page]
            return httpx.Response(
                200, json=self._page([{"slug": s} for s in chunk], page, per_page, len(slugs))
            )
        if method == "GET" and path.startswith("/api/recipes/"):
            slug = path.rsplit("/", 1)[1]
            if slug not in self.recipes:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=self.recipes[slug])
        if (method, path) == ("POST", "/api/parser/ingredients"):
            parsed = [
                {"input": text, "ingredient": self.parses.get(text, raw(text))}
                for text in body["ingredients"]
            ]
            return httpx.Response(200, json=parsed)
        if (method, path) == ("GET", "/api/households/mealplans"):
            start, end = params["start_date"][0], params["end_date"][0]
            items = [e for e in self.entries.values() if start <= e["date"] <= end]
            return httpx.Response(200, json=self._page(items, 1, len(items) or 1, len(items)))
        if (method, path) == ("POST", "/api/households/mealplans"):
            if self.create_entry_status != 201:
                return httpx.Response(self.create_entry_status, json={"detail": "boom"})
            entry_id = max(self.entries, default=0) + 1
            self.entries[entry_id] = {"id": entry_id, **body}
            return httpx.Response(201, json=self.entries[entry_id])
        if method == "DELETE" and path.startswith("/api/households/mealplans/"):
            return httpx.Response(200, json=self.entries.pop(int(path.rsplit("/", 1)[1])))
        raise AssertionError(f"MealieStub: unexpected {method} {request.url}")

    @staticmethod
    def _page(items: list[Json], page: int, per_page: int, total: int) -> Json:
        total_pages = -(-total // per_page) if total else 0
        return {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "items": items,
        }


@pytest.fixture
def stub() -> MealieStub:
    return MealieStub()


@pytest.fixture
def client(stub: MealieStub) -> HttpMealieClient:
    return HttpMealieClient(
        httpx.Client(base_url="http://mealie.test", transport=httpx.MockTransport(stub))
    )


def test_satisfies_the_mealie_client_protocol(client: HttpMealieClient) -> None:
    assert isinstance(client, MealieClient)


# ── import_url ───────────────────────────────────────────────────────────────


def test_import_url_returns_the_new_slug_without_importing_site_tags(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    assert client.import_url("https://www.budgetbytes.com/fajitas/") == "imported-recipe"

    sent = json.loads(stub.requests[-1].content)
    assert sent["url"] == "https://www.budgetbytes.com/fajitas/"
    assert sent["includeTags"] is False


@pytest.mark.parametrize("status", [400, 408, 422, 500])
def test_import_url_raises_value_error_when_mealie_cannot_scrape(
    client: HttpMealieClient, stub: MealieStub, status: int
) -> None:
    stub.import_status = status
    with pytest.raises(ValueError):
        client.import_url("https://www.budgetbytes.com/fajitas/")


def test_import_url_lets_an_auth_failure_through_as_an_http_error(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.import_status = 401
    with pytest.raises(httpx.HTTPStatusError):
        client.import_url("https://www.budgetbytes.com/fajitas/")


# ── get_recipe ───────────────────────────────────────────────────────────────


def test_get_recipe_maps_mealie_fields_onto_recipe_option(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["fajitas"] = recipe_json(
        "fajitas",
        name="Sheet-Pan Fajitas",
        orgURL="https://www.budgetbytes.com/fajitas/",
        recipeServings=4.0,
        prepTimeSeconds=1500,
        tags=[{"id": "t1", "name": BATCH_OK_TAG, "slug": BATCH_OK_TAG}],
        recipeInstructions=[{"text": "Slice."}, {"text": ""}, {"text": "Roast."}],
    )

    recipe = client.get_recipe("fajitas")

    assert recipe.name == "Sheet-Pan Fajitas"
    assert recipe.url == "https://www.budgetbytes.com/fajitas/"
    assert recipe.source == "budgetbytes.com"
    assert recipe.hands_on_min == 25
    assert recipe.servings == 4
    assert recipe.batch_ok is True
    assert recipe.fit_note == ""
    assert recipe.steps == ("Slice.", "Roast.")


def test_get_recipe_fills_gaps_for_a_hand_entered_recipe(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["mac"] = recipe_json(
        "mac", orgURL=None, recipeServings=0.0, recipeYieldQuantity=6.0, prepTimeSeconds=None
    )

    recipe = client.get_recipe("mac")

    assert (recipe.url, recipe.source) == ("", "mealie")
    assert recipe.servings == 6
    assert recipe.hands_on_min is None
    assert recipe.batch_ok is False


def test_get_recipe_uses_structured_ingredients_as_is_without_parsing(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["rice"] = recipe_json(
        "rice",
        recipeIngredient=[structured("brown rice", 2, "cup", note="rinsed"), structured("salt", 0)],
    )

    recipe = client.get_recipe("rice")

    assert recipe.ingredients == (
        Ingredient(name="brown rice", qty=2, unit="cup", note="rinsed"),
        Ingredient(name="salt", qty=None, unit=None),
    )
    assert not any(r.url.path == "/api/parser/ingredients" for r in stub.requests)


def test_get_recipe_parses_text_only_lines_in_one_call_and_keeps_order(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["bowl"] = recipe_json(
        "bowl",
        recipeIngredient=[
            structured("brown rice", 1, "cup"),
            raw("2 lb chicken thighs"),
            raw(""),
            raw("salt to taste"),
        ],
    )
    stub.parses["2 lb chicken thighs"] = structured("chicken thighs", 2, "pound")

    recipe = client.get_recipe("bowl")

    assert recipe.ingredients == (
        Ingredient(name="brown rice", qty=1, unit="cup"),
        Ingredient(name="chicken thighs", qty=2, unit="pound"),
        Ingredient(name="salt to taste", qty=None, unit=None),
    )
    parser_calls = [r for r in stub.requests if r.url.path == "/api/parser/ingredients"]
    assert len(parser_calls) == 1
    assert json.loads(parser_calls[0].content) == {
        "parser": "nlp",
        "ingredients": ["2 lb chicken thighs", "salt to taste"],
    }


def test_get_recipe_raises_key_error_for_an_unknown_slug(client: HttpMealieClient) -> None:
    with pytest.raises(KeyError):
        client.get_recipe("nope")


# ── list_by_tag ──────────────────────────────────────────────────────────────


def test_list_by_tag_filters_by_the_resolved_tag_id_across_pages(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    slugs = [f"recipe-{n:03d}" for n in range(150)]
    stub.tags["rotation"] = "tag-rotation-id"
    stub.tagged["tag-rotation-id"] = slugs
    stub.tagged["tag-other-id"] = ["not-this-one"]

    assert client.list_by_tag("rotation") == tuple(slugs)
    listed = [r for r in stub.requests if r.url.path == "/api/recipes"]
    assert len(listed) >= 2
    assert all(parse_qs(r.url.query.decode())["tags"] == ["tag-rotation-id"] for r in listed)


def test_list_by_tag_returns_empty_for_an_unknown_tag_without_listing_recipes(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    assert client.list_by_tag("no-such-tag") == ()
    assert not any(r.url.path == "/api/recipes" for r in stub.requests)


# ── set_meal_plan ────────────────────────────────────────────────────────────


@pytest.fixture
def three_recipes(stub: MealieStub) -> None:
    for slug in ("fajitas", "meatballs", "egg-bites"):
        stub.recipes[slug] = recipe_json(slug)


@pytest.mark.usefixtures("three_recipes")
def test_set_meal_plan_puts_each_recipe_on_the_cook_day_and_returns_the_date(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    ref = client.set_meal_plan(WEEK, ["fajitas", "meatballs"])

    assert ref == WEEK.isoformat()
    created = sorted(stub.entries.values(), key=lambda e: e["id"])
    assert [e["recipeId"] for e in created] == [
        stub.recipes["fajitas"]["id"],
        stub.recipes["meatballs"]["id"],
    ]
    assert all(e["date"] == WEEK.isoformat() for e in created)
    assert all(e["entryType"] == "dinner" for e in created)
    assert all(e["text"] == PLAN_ENTRY_MARKER for e in created)


@pytest.mark.usefixtures("three_recipes")
def test_set_meal_plan_replaces_only_its_own_entries(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    kept = stub.add_entry("fajitas", PLAN_ENTRY_MARKER)
    stub.add_entry("meatballs", PLAN_ENTRY_MARKER)
    steves = stub.add_entry("meatballs", "Steve added this by hand")

    client.set_meal_plan(WEEK, ["fajitas", "egg-bites"])

    on_plan = {(e["recipeId"], e["text"]) for e in stub.entries.values()}
    assert on_plan == {
        (stub.recipes["fajitas"]["id"], PLAN_ENTRY_MARKER),
        (stub.recipes["egg-bites"]["id"], PLAN_ENTRY_MARKER),
        (stub.recipes["meatballs"]["id"], "Steve added this by hand"),
    }
    assert kept in stub.entries and steves in stub.entries


@pytest.mark.usefixtures("three_recipes")
def test_set_meal_plan_is_a_no_op_when_rerun_with_the_same_recipes(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    client.set_meal_plan(WEEK, ["fajitas", "fajitas", "meatballs"])
    first = dict(stub.entries)
    writes_before = len(stub.writes())

    client.set_meal_plan(WEEK, ["fajitas", "meatballs"])

    assert len(first) == 2
    assert stub.entries == first
    assert len(stub.writes()) == writes_before


@pytest.mark.usefixtures("three_recipes")
def test_set_meal_plan_raises_key_error_for_an_unknown_slug_before_writing(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.add_entry("fajitas", PLAN_ENTRY_MARKER)

    with pytest.raises(KeyError):
        client.set_meal_plan(WEEK, ["meatballs", "nope"])

    assert stub.writes() == []


@pytest.mark.usefixtures("three_recipes")
def test_set_meal_plan_raises_when_mealie_rejects_a_write(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.create_entry_status = 500
    with pytest.raises(httpx.HTTPStatusError):
        client.set_meal_plan(WEEK, ["fajitas"])
