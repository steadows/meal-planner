import json
import uuid
from datetime import date
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from meals.config import Settings
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
        "prepTime": "20 minutes",
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
        self.import_raises: Exception | None = None  # raised in place of answering the import
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
            if self.import_raises is not None:
                raise self.import_raises
            if self.import_status != 201:
                return httpx.Response(self.import_status, json={"detail": "scrape failed"})
            return httpx.Response(201, json="imported-recipe")
        if (method, path) == ("GET", "/api/organizers/tags"):
            tags = [{"id": tag_id, "name": tag, "slug": tag} for tag, tag_id in self.tags.items()]
            return httpx.Response(200, json=self._slice(tags, params))
        if (method, path) == ("GET", "/api/recipes"):
            slugs = sorted(self.tagged.get(params["tags"][0], []))
            return httpx.Response(200, json=self._slice([{"slug": s} for s in slugs], params))
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

    @classmethod
    def _slice(cls, items: list[Json], params: dict[str, list[str]]) -> Json:
        per_page, page = int(params["perPage"][0]), int(params["page"][0])
        chunk = items[(page - 1) * per_page : page * per_page]
        return cls._page(chunk, page, per_page, len(items))

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


@pytest.mark.parametrize("status", [400, 408, 500])
def test_import_url_raises_value_error_when_mealie_cannot_scrape(
    client: HttpMealieClient, stub: MealieStub, status: int
) -> None:
    stub.import_status = status
    with pytest.raises(ValueError):
        client.import_url("https://www.budgetbytes.com/fajitas/")


# 401: a bad token. 422: a request body this client got wrong. Both are bugs to surface, not
# recipes that couldn't be saved.
@pytest.mark.parametrize("status", [401, 422])
def test_import_url_lets_an_auth_or_request_error_through_as_an_http_error(
    client: HttpMealieClient, stub: MealieStub, status: int
) -> None:
    stub.import_status = status
    with pytest.raises(httpx.HTTPStatusError):
        client.import_url("https://www.budgetbytes.com/fajitas/")


def test_import_url_raises_value_error_when_mealie_times_out(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.import_raises = httpx.ReadTimeout("timed out")
    with pytest.raises(ValueError):
        client.import_url("https://www.budgetbytes.com/fajitas/")


# Mealie is down or unreachable: a fault to surface. Only a read timeout means it's still scraping.
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_import_url_lets_a_connection_failure_through(
    client: HttpMealieClient, stub: MealieStub, error: type[httpx.TransportError]
) -> None:
    stub.import_raises = error("Mealie is unreachable")
    with pytest.raises(error):
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
        prepTime="20 minutes",
        prepTimeSeconds=1500,  # not in v3.28.0; wins over prepTime when a later Mealie sends it
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
        "mac", orgURL=None, recipeServings=0.0, recipeYieldQuantity=6.0, prepTime=None
    )

    recipe = client.get_recipe("mac")

    assert (recipe.url, recipe.source) == ("", "mealie")
    assert recipe.servings is None  # a yield counts cookies or loaves, not people
    assert recipe.hands_on_min is None
    assert recipe.batch_ok is False


# v3.28.0 sends prepTime as free text (its scraper writes "1 hour 30 minutes"), not seconds.
@pytest.mark.parametrize(
    ("prep_time", "minutes"),
    [
        pytest.param("1 hour 30 minutes", 90, id="scraper-format"),
        pytest.param("25 min", 25, id="abbreviated"),
        pytest.param("PT1H30M", 90, id="iso-8601"),
        pytest.param("45", 45, id="bare-number-is-minutes"),
        pytest.param("about half an hour", None, id="unreadable"),
        pytest.param(".5 hours", 30, id="leading-decimal-point"),
        pytest.param("1/2 hour", None, id="fraction-is-unknown-not-2-hours"),
    ],
)
def test_get_recipe_reads_hands_on_minutes_from_free_text_prep_time(
    client: HttpMealieClient, stub: MealieStub, prep_time: str, minutes: int | None
) -> None:
    stub.recipes["stew"] = recipe_json("stew", prepTime=prep_time)

    assert client.get_recipe("stew").hands_on_min == minutes


def test_get_recipe_reads_a_malformed_org_url_as_an_unknown_source(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["odd"] = recipe_json("odd", orgURL="http://[bad/")

    recipe = client.get_recipe("odd")

    assert (recipe.url, recipe.source) == ("http://[bad/", "mealie")


def test_get_recipe_reads_recipe_tags_by_slug_alone(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["chili"] = recipe_json(
        "chili", tags=[{"id": None, "name": BATCH_OK_TAG, "slug": BATCH_OK_TAG}]
    )

    assert client.get_recipe("chili").batch_ok is True


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


def test_get_recipe_uses_a_food_less_line_with_its_own_amount_as_is(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    line = {
        "food": None,
        "quantity": 3,
        "unit": {"name": "cup"},
        "note": "rice",
        "display": "3 cups rice",
        "originalText": "1 cup rice",
    }
    stub.recipes["pilaf"] = recipe_json("pilaf", recipeIngredient=[line])

    recipe = client.get_recipe("pilaf")

    assert recipe.ingredients == (Ingredient(name="rice", qty=3, unit="cup"),)
    assert not any(r.url.path == "/api/parser/ingredients" for r in stub.requests)


def test_get_recipe_parses_a_text_only_line_as_it_reads_now_not_as_first_imported(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["pilaf"] = recipe_json(
        "pilaf", recipeIngredient=[raw("2 cups rice") | {"originalText": "1 cup rice"}]
    )

    client.get_recipe("pilaf")

    [parser_call] = [r for r in stub.requests if r.url.path == "/api/parser/ingredients"]
    assert json.loads(parser_call.content)["ingredients"] == ["2 cups rice"]


@pytest.mark.parametrize(
    ("quantity", "display", "qty"),
    [
        pytest.param(1, "1", 1, id="with-amount"),
        pytest.param(0, "", None, id="no-amount-or-text"),
    ],
)
def test_get_recipe_names_a_linked_recipe_line_after_that_recipe(
    client: HttpMealieClient, stub: MealieStub, quantity: float, display: str, qty: float | None
) -> None:
    line = raw(display) | {
        "quantity": quantity,
        "originalText": None,
        "referencedRecipe": {"id": recipe_json("homemade-sauce")["id"], "name": "Homemade sauce"},
    }
    stub.recipes["enchiladas"] = recipe_json("enchiladas", recipeIngredient=[line])

    recipe = client.get_recipe("enchiladas")

    assert recipe.ingredients == (Ingredient(name="Homemade sauce", qty=qty),)
    assert not any(r.url.path == "/api/parser/ingredients" for r in stub.requests)


def test_get_recipe_raises_key_error_for_an_unknown_slug(client: HttpMealieClient) -> None:
    with pytest.raises(KeyError):
        client.get_recipe("nope")


# ── list_by_tag ──────────────────────────────────────────────────────────────


def test_list_by_tag_filters_by_the_resolved_tag_id_across_pages(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    slugs = [f"recipe-{n:03d}" for n in range(150)]
    stub.tags.update({f"tag-{n:03d}": f"tag-{n:03d}-id" for n in range(150)})
    stub.tags["rotation"] = "tag-rotation-id"  # on the second page of tags
    stub.tagged["tag-rotation-id"] = slugs
    stub.tagged["tag-other-id"] = ["not-this-one"]

    assert client.list_by_tag("rotation") == tuple(slugs)
    listed = [r for r in stub.requests if r.url.path == "/api/recipes"]
    assert len(listed) >= 2
    assert all(parse_qs(r.url.query.decode())["tags"] == ["tag-rotation-id"] for r in listed)


def test_list_by_tag_returns_empty_for_an_unknown_tag_without_listing_recipes(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.tags["rotation-old"] = "tag-old-id"  # a near miss: the slug must match exactly
    stub.tagged["tag-old-id"] = ["old-favourite"]

    assert client.list_by_tag("rotation") == ()
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
def test_set_meal_plan_collapses_duplicate_entries_of_its_own(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.add_entry("fajitas", PLAN_ENTRY_MARKER)
    stub.add_entry("fajitas", PLAN_ENTRY_MARKER)
    steves = stub.add_entry("fajitas", "Steve added this by hand")

    client.set_meal_plan(WEEK, ["fajitas"])

    ours = [e for e in stub.entries.values() if e["text"] == PLAN_ENTRY_MARKER]
    assert [e["recipeId"] for e in ours] == [stub.recipes["fajitas"]["id"]]
    assert steves in stub.entries


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


# ── guards (C2) ──────────────────────────────────────────────────────────────
# Seam map lane-c-mealie: `_require_public_url`, the slug and tag allowlist, `from_settings`.
# Mealie fetches import URLs from inside the home network, so only a public http(s) URL may reach
# it. Slugs and tags go into URL paths, so anything outside [A-Za-z0-9_-]+ is unknown, unsent.

NON_PUBLIC_URLS = [
    pytest.param("file:///etc/passwd", id="file-scheme"),
    pytest.param("ftp://example.com/r", id="ftp-scheme"),
    pytest.param("gopher://example.com/", id="gopher-scheme"),
    pytest.param("javascript:alert(1)", id="javascript-scheme"),
    pytest.param("httpx://example.com/r", id="http-prefixed-scheme"),
    pytest.param("www.budgetbytes.com/fajitas/", id="no-scheme"),
    pytest.param("", id="empty"),
    pytest.param("https:///path", id="no-host"),
    pytest.param("http://:80/", id="port-only"),
    pytest.param("https://user:pass@example.com/r", id="userinfo"),
    pytest.param("https://example.com@evil.example/r", id="userinfo-lookalike"),
    pytest.param("http://localhost:9925/api/users", id="localhost"),
    pytest.param("http://LOCALHOST/", id="localhost-uppercase"),
    pytest.param("http://localhost./", id="localhost-trailing-dot"),
    pytest.param("http://app.localhost/", id="localhost-subdomain"),
    pytest.param("http://nas.local/", id="mdns-local"),
    pytest.param("http://host.docker.internal/", id="internal-tld"),
    pytest.param("http://router.home.arpa/", id="home-arpa"),
    pytest.param("http://NAS.Local./", id="local-mixed-case-trailing-dot"),
    pytest.param("http://mealie:9000/", id="docker-service-name"),
    pytest.param("http://mealie./", id="single-label-trailing-dot"),
    pytest.param("http://router/", id="lan-name"),
    pytest.param("http://2130706433/", id="decimal-ip"),
    pytest.param("http://0x7f000001/", id="hex-ip"),
    pytest.param("http://127.0.0.1/x", id="loopback"),
    pytest.param("http://127.0.0.1./x", id="loopback-trailing-dot"),
    pytest.param("http://10.0.0.5/x", id="private-10"),
    pytest.param("http://172.16.0.1/x", id="private-172"),
    pytest.param("http://192.168.1.1:9925/", id="private-192-with-port"),
    pytest.param("http://169.254.169.254/x", id="link-local-metadata"),
    pytest.param("http://100.100.100.100/x", id="cgnat-tailscale"),
    pytest.param("http://0.0.0.0/x", id="unspecified"),
    pytest.param("http://224.0.0.1/x", id="multicast"),
    pytest.param("http://255.255.255.255/x", id="broadcast"),
    pytest.param("http://[::1]/x", id="ipv6-loopback"),
    pytest.param("http://[::]/x", id="ipv6-unspecified"),
    pytest.param("http://[fe80::1]:8080/x", id="ipv6-link-local-with-port"),
    pytest.param("http://[fc00::1]/x", id="ipv6-ula"),
    pytest.param("http://[::ffff:127.0.0.1]/x", id="ipv4-mapped-loopback"),
    pytest.param("http://[::ffff:192.168.1.1]/x", id="ipv4-mapped-private"),
    pytest.param("http://[::ffff:100.100.100.100]/x", id="ipv4-mapped-cgnat"),
    # Reserved IPv6 that Python still calls global: ::/8 embeddings and the NAT64 prefix.
    pytest.param("http://[::7f00:1]/x", id="ipv4-compatible-loopback"),
    pytest.param("http://[::ffff:0:7f00:1]/x", id="siit-loopback"),
    pytest.param("http://[64:ff9b::a9fe:a9fe]/x", id="nat64-metadata"),
    pytest.param("http://0x7f.0.0.1/", id="inet-aton-hex"),
    pytest.param("http://0177.0.0.1/", id="inet-aton-octal"),
    pytest.param("http://127.1/", id="inet-aton-short"),
    # The host is judged as the fetcher (httpx) parses it, IDNA-normalised: this is 127.0.0.1.
    pytest.param("http://127。0.0.1/", id="idna-dot-loopback"),
]

PUBLIC_URLS = [
    pytest.param("https://www.budgetbytes.com/sheet-pan-fajitas/", id="recipe-site"),
    pytest.param("http://example.com/recipe", id="plain-http"),
    pytest.param("https://sub.example.co.uk:8443/r?id=1#top", id="port-query-fragment"),
    pytest.param("https://example.com./r", id="fqdn-trailing-dot"),
    pytest.param("https://8.8.8.8/r", id="global-ipv4"),
    pytest.param("https://[2606:4700:4700::1111]/r", id="global-ipv6"),
    pytest.param("https://bücher.example/r", id="idn-host"),
    pytest.param("https://www.localfoods.com/r", id="local-as-substring-not-suffix"),
    # .example never resolves (RFC 2606), so a guard that looks the host up would refuse it.
    pytest.param("https://recipes.example/r", id="unresolvable-no-dns-lookup"),
]


@pytest.mark.parametrize("url", NON_PUBLIC_URLS)
def test_import_url_refuses_a_non_public_url_before_contacting_mealie(
    client: HttpMealieClient, stub: MealieStub, url: str
) -> None:
    with pytest.raises(ValueError):
        client.import_url(url)

    assert stub.requests == []


@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_import_url_sends_a_public_url_to_mealie_unchanged(
    client: HttpMealieClient, stub: MealieStub, url: str
) -> None:
    assert client.import_url(url) == "imported-recipe"

    [request] = stub.requests
    assert json.loads(request.content)["url"] == url


BAD_SLUGS = [
    pytest.param("../users/self", id="traversal"),
    pytest.param("..", id="dot-dot"),
    pytest.param(".", id="dot"),
    pytest.param("a/b", id="slash"),
    pytest.param("a%2Fb", id="encoded-slash"),
    pytest.param("fajitas?x=1", id="query"),
    pytest.param("fajitas#x", id="fragment"),
    pytest.param("fa jitas", id="space"),
    pytest.param("", id="empty"),
    pytest.param("fajitas\n", id="trailing-newline"),
    pytest.param("fajítas", id="non-ascii"),
]


@pytest.mark.usefixtures("three_recipes")
@pytest.mark.parametrize("slug", BAD_SLUGS)
def test_get_recipe_treats_a_slug_outside_the_allowlist_as_unknown(
    client: HttpMealieClient, stub: MealieStub, slug: str
) -> None:
    with pytest.raises(KeyError):
        client.get_recipe(slug)

    assert stub.requests == []


@pytest.mark.usefixtures("three_recipes")
@pytest.mark.parametrize("slug", BAD_SLUGS)
def test_set_meal_plan_treats_a_slug_outside_the_allowlist_as_unknown(
    client: HttpMealieClient, stub: MealieStub, slug: str
) -> None:
    with pytest.raises(KeyError):
        client.set_meal_plan(WEEK, ["fajitas", slug])

    assert stub.writes() == []
    # Resolving the good slug first is allowed; nothing may be sent for the bad one.
    assert {str(r.url) for r in stub.requests} - {"http://mealie.test/api/recipes/fajitas"} == set()


@pytest.mark.parametrize("tag", BAD_SLUGS)
def test_list_by_tag_treats_a_tag_outside_the_allowlist_as_unknown(
    client: HttpMealieClient, stub: MealieStub, tag: str
) -> None:
    assert client.list_by_tag(tag) == ()
    assert stub.requests == []


def test_a_slug_may_mix_case_digits_underscores_and_hyphens(
    client: HttpMealieClient, stub: MealieStub
) -> None:
    stub.recipes["Sheet_Pan-Fajitas-2"] = recipe_json("Sheet_Pan-Fajitas-2")

    client.get_recipe("Sheet_Pan-Fajitas-2")

    assert [r.url.path for r in stub.requests] == ["/api/recipes/Sheet_Pan-Fajitas-2"]


TOKEN = "tok-123"


def test_from_settings_sends_the_bearer_token_to_the_configured_host(stub: MealieStub) -> None:
    settings = Settings(mealie_url="http://mealie.test", mealie_token=SecretStr(TOKEN))
    client = HttpMealieClient.from_settings(settings, transport=httpx.MockTransport(stub))
    stub.recipes["fajitas"] = recipe_json("fajitas")

    client.get_recipe("fajitas")

    [request] = stub.requests
    assert request.url.host == "mealie.test"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_from_settings_keeps_a_path_prefix_in_mealie_url() -> None:
    seen: list[httpx.Request] = []

    def not_found(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404, json={"detail": "not found"})

    settings = Settings(mealie_url="http://mealie.test/mealie", mealie_token=SecretStr(TOKEN))
    client = HttpMealieClient.from_settings(settings, transport=httpx.MockTransport(not_found))

    with pytest.raises(KeyError):
        client.get_recipe("fajitas")
    assert [r.url.path for r in seen] == ["/mealie/api/recipes/fajitas"]


def test_from_settings_without_a_token_raises_naming_mealie_token() -> None:
    settings = Settings(mealie_url="http://mealie.test", mealie_token=None)

    with pytest.raises(RuntimeError, match="MEALIE_TOKEN"):
        HttpMealieClient.from_settings(settings)


# A real transport's error for an illegal header value quotes the whole header, token included,
# so a token that isn't an RFC 6750 b64token must be refused before any header is built.
@pytest.mark.parametrize(
    "token",
    [
        pytest.param("tokABC\nDEF", id="newline"),
        pytest.param("tokABC DEF", id="space"),
        pytest.param("tokABC\x00DEF", id="nul"),
    ],
)
def test_from_settings_refuses_a_malformed_token_without_echoing_it(token: str) -> None:
    settings = Settings(mealie_url="http://mealie.test", mealie_token=SecretStr(token))

    with pytest.raises(RuntimeError, match="MEALIE_TOKEN is malformed") as raised:
        HttpMealieClient.from_settings(settings)

    error = raised.value
    shown = f"{error} {error.__cause__!r} {error.__context__!r}"
    assert "tokABC" not in shown and "DEF" not in shown


@pytest.mark.parametrize(
    ("token", "sent"),
    [
        pytest.param("tok-123 \n", "tok-123", id="surrounding-whitespace-stripped"),
        pytest.param("eyJhbGciOi.eyJzdWIi.sig-_x", "eyJhbGciOi.eyJzdWIi.sig-_x", id="jwt-shaped"),
    ],
)
def test_from_settings_accepts_a_well_formed_token(stub: MealieStub, token: str, sent: str) -> None:
    settings = Settings(mealie_url="http://mealie.test", mealie_token=SecretStr(token))
    client = HttpMealieClient.from_settings(settings, transport=httpx.MockTransport(stub))
    stub.recipes["fajitas"] = recipe_json("fajitas")

    client.get_recipe("fajitas")

    assert stub.requests[-1].headers["Authorization"] == f"Bearer {sent}"


@pytest.mark.usefixtures("isolated_settings")
def test_from_settings_defaults_to_the_process_settings(
    stub: MealieStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEALIE_URL", "http://env-mealie.test")
    monkeypatch.setenv("MEALIE_TOKEN", "env-tok")
    stub.recipes["fajitas"] = recipe_json("fajitas")

    HttpMealieClient.from_settings(transport=httpx.MockTransport(stub)).get_recipe("fajitas")

    [request] = stub.requests
    assert request.url.host == "env-mealie.test"
    assert request.headers["Authorization"] == "Bearer env-tok"


def test_the_token_stays_out_of_repr_and_error_text(stub: MealieStub) -> None:
    settings = Settings(mealie_url="http://mealie.test", mealie_token=SecretStr(TOKEN))
    client = HttpMealieClient.from_settings(settings, transport=httpx.MockTransport(stub))
    assert TOKEN not in repr(client)

    stub.import_status = 401
    with pytest.raises(httpx.HTTPStatusError) as unauthorized:
        client.import_url("https://www.budgetbytes.com/fajitas/")
    assert TOKEN not in str(unauthorized.value)

    stub.import_status = 400
    with pytest.raises(ValueError) as unscrapable:
        client.import_url("https://www.budgetbytes.com/fajitas/")
    assert TOKEN not in str(unscrapable.value)
