---
type: research
topic: mealie-v3-api
researched: 2026-09-26
by: mealie
sources:
  - https://github.com/mealie-recipes/mealie/releases
  - https://github.com/mealie-recipes/mealie/releases/tag/v3.28.0
  - https://demo.mealie.io/openapi.json
  - https://mealie.io/documentation/getting-started/api-usage/
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/routes/recipe/recipe_crud_routes.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/schema/recipe/recipe.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/schema/recipe/recipe_ingredient.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/routes/parser/ingredient_parser.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/routes/households/controller_mealplan.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/schema/meal_plan/new_meal.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/routes/organizers/controller_tags.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/schema/response/pagination.py
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/schema/recipe/request_helpers.py
  - https://github.com/mealie-recipes/mealie/blob/mealie-next/docs/docs/documentation/getting-started/installation/sqlite.md
  - https://github.com/joostlek/python-mealie
  - https://pypi.org/project/aiomealie/
  - https://github.com/mealie-recipes/mealie/issues/8334
  - https://github.com/mealie-recipes/mealie/issues/4789
  - https://github.com/mealie-recipes/mealie/security/advisories
  - https://securitylab.github.com/advisories/GHSL-2023-225_GHSL-2023-226_Mealie/
  - https://osv.dev/vulnerability/CVE-2024-31991
  - https://osv.dev/vulnerability/CVE-2024-31993
  - https://www.strix.ai/cve/CVE-2024-31991
  - https://www.strix.ai/cve/CVE-2024-31992
  - https://www.strix.ai/cve/CVE-2024-31993
  - https://www.strix.ai/cve/CVE-2026-71210
  - https://www.strix.ai/cve/CVE-2026-94028
  - https://app.opencve.io/cve/CVE-2026-71210
  - https://cve.imfht.com/intel/805378
  - https://cve.imfht.com/intel/805380
  - https://cve.imfht.com/intel/805381
  - https://github.com/mealie-recipes/mealie/issues/7831
  - https://github.com/mealie-recipes/mealie/pull/8326
  - https://github.com/mealie-recipes/mealie/pull/8376
  - https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/pkgs/safehttp/transport.py
---

# Mealie v3 REST API — research note for a sync httpx client

**Method note:** all route/schema claims below came from fetching raw source files off
the `mealie-next` branch (the repo's default/dev branch) via `raw.githubusercontent.com`,
not from cloning the repo or checking out the `v3.28.0` tag specifically. `mealie-next`
should match a very recent stable tag closely but I did not diff it against the
`v3.28.0` tag directly — treat file-level details (exact field order, docstrings) as
"as of `mealie-next` on 2026-09-26," not pinned to the tag. Where a WebFetch summarized
a large file rather than me reading raw content directly, I've marked it. Time-boxed to
~6 minutes (extended slightly for the SSRF follow-up, §9); several items are marked
UNVERIFIED where the tool truncated content or 404'd.

## 0. Version

- **Newest stable tag as of 2026-09-26: `v3.28.0`**, released 2026-09-24 (plan's pin of
  `v3.27.0` from 2026-09-22 is one release behind).
  Source: https://github.com/mealie-recipes/mealie/releases/tag/v3.28.0
- v3.28.0 highlights: food seed data overhaul (foods now link to labels; seed list
  slimmed from ~2,000 to ~500), drag-and-drop image import from websites into recipe
  instructions, upgraded to Python 3.14, and CHIPS support for embedded/iframe auth
  (relevant to Home Assistant, not to a plain API client).
  No breaking API changes were mentioned in the release summary I fetched — UNVERIFIED
  against the full changelog/diff (I read a WebSearch summary of the release page, not
  the full release body).

## 1. Auth

- Long-lived API tokens are created by a user at `/user/profile/api-tokens` in the web
  UI (no stated expiry, user-revocable).
- Send as a standard bearer header: `Authorization: Bearer <token>`.
- Source: https://mealie.io/documentation/getting-started/api-usage/ (WebSearch summary;
  I did not fetch this page directly, so treat the exact doc wording as UNVERIFIED, but
  this matches the well-established Mealie convention referenced across GitHub
  discussions and the interactive `/docs` Swagger UI, which shows a bearer
  `HTTPBearer` security scheme).

## 2. Create recipe from URL

Source: `mealie/routes/recipe/recipe_crud_routes.py` (raw fetch of full route file).

Full list of recipe-creation routes on this router (mounted under `/api/recipes`):

| Method | Path | Function |
|---|---|---|
| POST | `/test-scrape-url` | `test_parse_recipe_url` |
| POST | `/create/html-or-json` | `create_recipe_from_html_or_json` |
| POST | `/create/html-or-json/stream` | `create_recipe_from_html_or_json_stream` |
| POST | `/create/url` | `parse_recipe_url` |
| POST | `/create/url/stream` | `parse_recipe_url_stream` |
| POST | `/create/ai` | `create_recipe_with_ai` |
| POST | `/create/ai/stream` | `create_recipe_with_ai_stream` |
| POST | `/create/url/bulk` | `parse_recipe_url_bulk` |
| POST | `/create/zip` | `create_recipe_from_zip` |
| POST | `/create/image` | `create_recipe_from_image` |

**`POST /api/recipes/create/url`** — the one we want:
- Request body model `ScrapeRecipe`: `url: str`, `include_tags: bool | None` (also
  reported as `includeTags` in one WebSearch hit showing camelCase — Mealie's API uses
  camelCase aliases at the JSON boundary, so send `{"url": "...", "includeTags": false}`;
  UNVERIFIED exact alias name — confirm against `/docs` Swagger UI on your instance
  before shipping, since one tool pass also reported an `include_categories` field that
  I could not confirm from primary source).
- **Response: `str`** — just the new recipe's **slug** (plain string, not a full recipe
  object). Status **201** on success.
- Failure modes seen in the route body: **400** if scraping fails / no recipe data
  could be extracted from the page; **408** if scraping times out; **500** for
  unexpected/unknown scraper errors.
- **Duplicate URL import behavior: UNVERIFIED.** I could not find or fetch the slug-
  generation/collision logic (likely in `mealie/services/recipe/recipe_service.py` or a
  slug utility) in the time budget. Mealie slugs are generated from the recipe name, and
  general Mealie behavior elsewhere (asset/recipe creation) is to de-duplicate slugs
  with a numeric suffix (`-1`, `-2`, ...) rather than error — but for this specific
  create-from-url path, whether re-importing the same URL (a) creates a second recipe
  with a suffixed slug, (b) errors, or (c) returns the existing recipe, was **not
  confirmed against source in this pass**. Test this empirically against your own
  instance before relying on it (import a URL twice and check).
- **Separate "test scrape" endpoint: yes** — `POST /api/recipes/test-scrape-url`.
  Request model `ScrapeRecipeTest` (`url: str`, `use_openai: bool | None`). Returns the
  raw scraped schema.org data as JSON (or a string message if the scraper couldn't
  extract anything), without persisting a recipe. Same 408 timeout behavior. Useful for
  a "preview before import" flow or for our error-handling tests.
- There's also `POST /api/recipes/create/url/bulk` for importing many URLs at once
  (returns via the job/report system per other Mealie docs — UNVERIFIED shape) and
  `.../create/url/stream` / `.../create/html-or-json/stream`, which are Server-Sent
  Events (SSE) variants for progress reporting on long scrapes — not needed for a
  simple sync client, skip these.

## 3. Get recipe by slug

- **`GET /api/recipes/{slug}`** → response model `Recipe`. The path param name is
  `slug` but Mealie's router also accepts a recipe UUID in the same slot for this
  endpoint (per `handle_exceptions()` wrapping — UNVERIFIED exact fallback logic, but
  consistent with Mealie's general "slug-or-id" convention on `{slug}` routes elsewhere).
- 404 behavior: UNVERIFIED exact status/body — the fetch only showed the route is
  wrapped in a generic `handle_exceptions()` helper that maps internal exceptions to
  HTTPExceptions; a "not found" is presumed to be a **404** (standard FastAPI/Mealie
  convention seen elsewhere in the codebase) but I did not confirm the specific
  exception-to-status mapping for a missing recipe. Confirm empirically.

Field mapping from `mealie/schema/recipe/recipe.py` (`Recipe` class):

| Field (Python name → JSON alias) | Type | Notes |
|---|---|---|
| `name` | `str \| None` | |
| `org_url` → `orgURL` | `str \| None` | source URL, camelCase alias confirmed |
| `recipe_servings` → `recipeServings` | `float` | numeric, not a string |
| `recipe_yield` → `recipeYield` | `str \| None` | free text (e.g. "4 servings") |
| `recipe_yield_quantity` | `float` | numeric companion to `recipe_yield` |
| `prep_time` → `prepTime` | `str \| None` | **free text**, not ISO 8601 (e.g. "15 minutes") |
| `total_time` → `totalTime` | `str \| None` | free text |
| `perform_time` → `performTime` | `str \| None` | free text; note there's no plain `cook_time` field name pairing exactly with `performTime` in the JSON — `performTime` is the field Mealie's UI labels "Cook Time" |
| `total_time_seconds`, `prep_time_seconds`, `perform_time_seconds` | `DurationSeconds \| None` | **these are the actual ISO-8601-duration-parsed numeric seconds**, derived server-side from the free-text fields above. If we need machine-usable durations, use the `*_seconds` fields, not `prepTime`/`totalTime`/`performTime`. |
| `tags` | `list[RecipeTag] \| None` | each item shape: `id (UUID)`, `name`, `slug` (per `RecipeTagResponse`/`RecipeTag`; confirmed slug/id via the tags controller below) |
| `recipe_category` | `list[RecipeCategory] \| None` | same id/name/slug shape as tags |
| `settings` | `RecipeSettings \| None` | includes `disableAmount` (bool) among other UI toggles — exact full field list of `RecipeSettings` **UNVERIFIED**, only confirmed the field name exists |
| `extras` | `dict \| None` | free-form user-defined key/value metadata, **not** populated by a plain URL scrape |
| `recipe_ingredient` → `recipeIngredient` | `list[RecipeIngredient]` | see §4 |
| `recipe_instructions` → `recipeInstructions` | `list[RecipeStep] \| None` | each step: title, text, and ingredient references — exact `RecipeStep` field list **UNVERIFIED** (not fetched); expect at least `id`, `title`, `text`, `ingredientReferences` |
| `nutrition` | `Nutrition \| None` | not requested but present |
| `assets`, `notes`, `comments`, `tools` | lists | not requested |

## 4. Ingredient parsing

**What a plain URL import produces (`recipeIngredient` items):**

`RecipeIngredient` fields (from `mealie/schema/recipe/recipe_ingredient.py`):

| Field (alias) | Type | Populated by a plain schema.org URL scrape? |
|---|---|---|
| `title` | `str \| None` | usually not (used for ingredient section headers) |
| `note` | `str \| None` | sometimes, if the source separates it |
| `original_text` → `originalText` | `str \| None` | **yes — always** the raw scraped ingredient line |
| `display` | `str` | yes — the rendered text (may equal `originalText` if unparsed) |
| `quantity` | `float \| None` (`NoneFloat`) | **only if Mealie's NLP parser successfully parsed the line** |
| `unit` | `IngredientUnit \| None` | only if parsed |
| `food` | `IngredientFood \| None` | only if parsed |
| `reference_id` | `UUID` | always (internal reference key, used to link instructions to ingredients) |
| `substitutions` | list | rarely populated on import |

**Key finding: a plain schema.org/JSON-LD URL scrape does NOT run Mealie's structured
ingredient parser automatically as far as I could confirm** — Mealie's scraper
(`recipe-scrapers`-based) extracts the raw ingredient line into `originalText`/`display`,
and structured `quantity`/`unit`/`food` extraction is a **separate, additional NLP/parser
step**. Whether `create/url` invokes that parser inline by default is **UNVERIFIED** —
I could not fetch the scraper service code in the time budget. Given the existence of a
dedicated parser endpoint (below) and known GitHub issue chatter about ingredient
parsing quality, **assume structured fields may be null/sparse after a plain URL import**
and be ready to call the parser endpoint yourself as a follow-up if the client needs
structured quantity/unit/food.

**Dedicated parser endpoints** (`mealie/routes/parser/ingredient_parser.py`, confirmed
by direct raw fetch):

| Method | Path | Request model | Response model |
|---|---|---|---|
| POST | `/api/parser/ingredient` | `IngredientRequest` (`parser: RegisteredParser = nlp`, `ingredient: str`) | `ParsedIngredient` |
| POST | `/api/parser/ingredients` | `IngredientsRequest` (`parser: RegisteredParser = nlp`, `ingredients: list[str]`) | `list[ParsedIngredient]` |

- `RegisteredParser` enum values: **UNVERIFIED exact members** — the field defaults to
  `nlp`; the task description asked about `nlp`/`brute`/`openai` strategies, which
  matches Mealie's known parser backends from general docs/community knowledge, but I
  did not fetch the enum definition directly to confirm all three values exist under
  those exact names in this version. Confirm against your instance's `/docs` before
  hardcoding a value other than the default.
- `ParsedIngredient` response shape: `input: str | None` (echo of what was sent),
  `confidence: IngredientConfidence` (parser confidence score/object), `ingredient:
  RecipeIngredient` (the structured result, same shape as §3/§4 above).
- **Does it persist anything?** These are stateless parse-only endpoints — they return
  a `RecipeIngredient`-shaped object but are not attached to any recipe id in the
  request, so calling them does **not** by itself save anything to a recipe. To
  persist parsed results you'd `PATCH`/`PUT` the recipe's `recipeIngredient` list
  afterward. (This inference follows from the endpoint having no recipe-id path param;
  I did not find documentation stating it explicitly, so treat "no persistence" as
  reasoned-but-UNVERIFIED-by-explicit-doc.)

## 5. List recipes / tag filter / pagination

`GET /api/recipes` (from `recipe_crud_routes.py`'s `get_all`, cross-referenced with a
WebFetch summary of the OpenAPI spec and `request_helpers.py`):

- Pagination envelope (from `mealie/schema/response/pagination.py`, confirmed by raw
  fetch): response is `PaginationBase`-shaped: `page`, `per_page`, `total`,
  `total_pages`, `items` (the list), `next`, `previous` (next/previous are URLs, not
  page numbers). Camel-case aliasing is expected at the JSON boundary
  (`perPage`, `totalPages`) consistent with the rest of the API — **UNVERIFIED exact
  alias casing for this specific model**, but matches the pattern seen everywhere else.
- Query params on the query side: `page` (default 1), `per_page`/`perPage` (default
  varies — saw both 10 and 50 reported across different fetches, **UNVERIFIED exact
  default**), `order_by`, `order_direction` (`asc`/`desc`), `query_filter`,
  `pagination_seed` (required when `order_by=random`).
- **`perPage=-1` returning all results: UNVERIFIED.** This is a widely-referenced
  Mealie convention from community docs/other integrations, but I did not find it
  stated in the primary source I fetched in this pass. Common enough that it's worth
  trying, but write a fallback (loop pages) in case it's not honored on `v3.28.0`.
- **Tag filter (`tags` param): accepts tag SLUGS**, based on a WebSearch-confirmed
  example (`GET /api/recipes?tags=protein-high&perPage=20&page=1`) — not names, not
  raw UUIDs, though Mealie's `QueryFilterBuilder` is also described as supporting
  UUID-based filtering via the more general `queryFilter` expression syntax
  separately. **Known bug**: GitHub issues #8334 and #4789 report that filtering by
  multiple tags/categories, or tag filters on cookbooks, can under-return matching
  recipes — worth a note if we filter by tag and get suspiciously few results; verify
  counts independently rather than trusting the filtered response as exhaustive.
- **Resolving a tag slug to its id:** yes — `GET /api/organizers/tags/slug/{tag_slug}`
  (confirmed by direct raw fetch of `mealie/routes/organizers/controller_tags.py`),
  response model `RecipeTagResponse` (includes `id`). Full tag controller route table:

| Method | Path | Function |
|---|---|---|
| GET | `/api/organizers/tags` | `get_all` |
| GET | `/api/organizers/tags/empty` | `get_empty_tags` |
| POST | `/api/organizers/tags/merge` | `merge_tags` |
| GET | `/api/organizers/tags/{item_id}` | `get_one` |
| POST | `/api/organizers/tags` | `create_one` |
| PUT | `/api/organizers/tags/{item_id}` | `update_one` |
| DELETE | `/api/organizers/tags/{item_id}` | `delete_recipe_tag` |
| GET | `/api/organizers/tags/slug/{tag_slug}` | `get_one_by_slug` |

## 6. Meal plans (households)

Source: raw fetch of `mealie/routes/households/controller_mealplan.py` and
`mealie/schema/meal_plan/new_meal.py`. Mounted under `/api/households/mealplans`
(v2+ moved these under `households`, confirmed).

| Method | Path | Function | Notes |
|---|---|---|---|
| GET | `/api/households/mealplans` | `get_all` | query params `start_date`, `end_date` (both optional dates) plus standard `PaginationQuery` params (`page`, `per_page`, etc.) — response is the standard `PaginationBase` envelope wrapping `ReadPlanEntry` items |
| GET | `/api/households/mealplans/today` | `get_todays_meals` | no params |
| GET | `/api/households/mealplans/{item_id}` | `get_one` | `item_id: int` |
| POST | `/api/households/mealplans` | `create_one` | body = `CreatePlanEntry` |
| POST | `/api/households/mealplans/random` | `create_random_meal` | body = `CreateRandomEntry` (`date`, `entry_type`); picks a recipe per household meal-plan rules — **avoid this for our client**, per task instructions, since it depends on configured household rules we don't control |
| PUT | `/api/households/mealplans/{item_id}` | `update_one` | body = `UpdatePlanEntry` |
| DELETE | `/api/households/mealplans/{item_id}` | `delete_one` | `item_id: int` (note: entry ids are **ints**, not UUIDs) |

`CreatePlanEntry` fields (confirmed by raw fetch of `new_meal.py`):
- `date: date`
- `entry_type: PlanEntryType` (JSON alias `entryType`)
- `title: str`
- `text: str`
- `recipe_id: UUID | None` (JSON alias `recipeId`) — **yes, this is the recipe's
  UUID** (`Recipe.id`), not its slug.

`PlanEntryType` enum allowed values (confirmed): `breakfast`, `lunch`, `dinner`, `side`,
`snack`, `drink`, `dessert`.

`ReadPlanEntry` (list/get response shape) additionally includes: `id: int`,
`group_id: UUID`, `user_id: UUID`, `household_id: UUID`, and an embedded
`recipe: RecipeSummary | None` when a `recipe_id` is set.

**No separate "week plan" object** — Mealie models meal plans purely as a flat
collection of per-day/per-entry-type entries (`ReadPlanEntry` rows), filterable by
`start_date`/`end_date`; there is no week-grouping resource to fetch/create. There is
also a `controller_mealplan_rules.py` for household meal-plan **rules** (used by the
`/random` endpoint) — not needed for a client that only writes explicit entries; skip
both `/random` and the rules controller.

## 7. Existing Python client libraries

- **`aiomealie`** (PyPI: https://pypi.org/project/aiomealie/, source:
  https://github.com/joostlek/python-mealie) — the Home Assistant integration's
  client. **Async-only** (built on `aiohttp`, no sync wrapper). **MIT licensed.**
  Latest version at research time: **2.1.2, released 2026-09-20** — actively
  maintained (844 commits on `main`, recent release). Supports Mealie v2+.
  I was **unable to fetch its module listing or README endpoint coverage directly**
  (both attempts 404'd — likely wrong path/branch name under time pressure), so exact
  coverage of recipe-create-from-url, meal-plan CRUD, and the ingredient parser is
  **UNVERIFIED**. It's built primarily to serve Home Assistant's read-oriented
  integration (shopping lists, meal plans, recipe summaries for display), so parser and
  create-from-url support should not be assumed present — check its
  `aiomealie/` module contents directly before depending on it, if reuse is on the table.
- Other PyPI hits found by name only, not evaluated for quality: `python-mealie-api`
  (alpha, `0.0.1a0`), `mealie-client`, `mealieapi` (`0.0.1`) — all look like early/
  abandoned single-version packages based on version numbers alone; **not
  investigated further** given the time budget and because none looked more mature
  than `aiomealie`.

**Recommendation: do not reuse `aiomealie` (or the other options) — write the ~150-line
sync httpx client as planned.** Reasoning:
1. `aiomealie` is async (`aiohttp`); our stated requirement is a **synchronous** httpx
   client, so adopting it means either running an event loop just to call it (defeats
   the point of "sync client") or forking/wrapping it — more integration work than
   writing the handful of endpoints we actually need (create-from-url, get-by-slug,
   list-by-tag, tag-slug lookup, 3 meal-plan calls).
2. It's designed around Home Assistant's read-heavy use case; our two write-heavy paths
   (create recipe from URL, create meal-plan entry) are exactly the parts I could not
   confirm it supports.
3. The other PyPI packages are unmaintained/alpha and riskier than hand-rolling against
   a well-documented FastAPI-generated OpenAPI schema.
4. A hand-rolled client also gets us full control over error handling (400/408/500 on
   scrape failure) and the specific field mapping quirks noted in §3/§4 above (free-text
   vs `*_seconds` duration fields, sparse ingredient parsing) — worth owning directly
   rather than debugging through someone else's abstraction.

## 8. Docker Compose (SQLite)

Confirmed against https://github.com/mealie-recipes/mealie/blob/mealie-next/docs/docs/documentation/getting-started/installation/sqlite.md
(fetched directly):

```yaml
services:
  mealie:
    image: ghcr.io/mealie-recipes/mealie:v3.28.0
    container_name: mealie
    restart: always
    ports:
        - "9925:9000"        # host:container — container is 9000, confirmed
    deploy:
      resources:
        limits:
          memory: 1000M
    volumes:
      - mealie-data:/app/data/
    environment:
      ALLOW_SIGNUP: "false"
      PUID: 1000
      PGID: 1000
      TZ: America/Anchorage
      BASE_URL: https://mealie.yourdomain.com

volumes:
  mealie-data:
```

- **Container port is still 9000**, volume is still `/app/data` — matches the plan's
  assumptions. Update the pinned tag from `v3.27.0` → **`v3.28.0`**.
- **`ALLOW_SIGNUP`, `PUID`, `PGID`, `TZ`, `BASE_URL`** all still present and unchanged
  in shape/name.
- The docs page explicitly notes only these five env vars are shown on the SQLite quick
  -start page and points to a separate "Backend Configuration" doc for the full list —
  **I did not fetch that full backend-config page**, so any v3-specific *new required*
  env var beyond these five is **UNVERIFIED**; nothing in the v3.28.0 release notes I
  found mentioned a new required env var (the notable v3.28.0 backend change was
  Python 3.14 + CHIPS cookie support, not a new required setting), but confirm against
  `docs/docs/documentation/getting-started/installation/backend-config.md` (not
  fetched) before finalizing the compose file if you want full coverage of optional
  vars (e.g. workers/concurrency settings did NOT appear on this page, contrary to
  what I searched for — treat `MAX_WORKERS`/`WEB_CONCURRENCY` as unconfirmed for this
  version).
- No changed/new **required** env var found in this version relative to the plan's
  existing set — UNVERIFIED beyond what's stated above given time constraints.

## 9. SSRF protection on the URL scraper

**Yes — Mealie has a purpose-built SSRF guard for outbound scraper/image requests, but
it has a real CVE history, including one still-ambiguous fix as of this research pass.
Do not treat the guard as a substitute for network-level egress controls.**

### Current mechanism (confirmed by direct raw fetch)

Source: https://raw.githubusercontent.com/mealie-recipes/mealie/mealie-next/mealie/pkgs/safehttp/transport.py
(current `mealie-next`, i.e. very close to `v3.28.0` — not diffed against the exact tag).

- Mealie routes outbound recipe-scrape and image-scrape requests through a custom
  `AsyncSafeTransport` class rather than a bare `httpx`/`requests` call.
- It **blocks private, loopback, link-local, multicast, reserved, and unspecified IP
  ranges**: the fetched source shows a guard equivalent to
  `if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or
  ip.is_reserved or ip.is_unspecified: <block>`, called from a `_validate()` step
  before any connection is opened. This covers RFC1918/loopback/link-local
  (169.254.0.0/16, which includes the AWS/GCP metadata IP 169.254.169.254) and IPv6
  equivalents, based on Python's `ipaddress` module semantics.
- **Redirects are followed, and each hop is re-validated**, not just the initial URL —
  the docstring text I retrieved states redirects are "followed sequentially" and
  "re-validated per hop," and structurally this is enforced because
  `AsyncSafeTransport.handle_async_request()` (which calls `_validate()`) runs on every
  request `httpx` issues while following a redirect chain, not only on the first one.
  I did not independently step through `httpx`'s redirect-following internals to
  confirm no gap exists there — treat the "every hop" claim as sourced from the
  in-repo docstring, not from independently tracing the control flow.
- As of the current source, outbound connections appear to **pin to the specific
  validated IP address(es)** rather than re-resolving the hostname at connect time
  (see PR below) — this is the fix for the DNS-rebinding class of bug (§ below).

### Known CVEs / advisories (dates are as reported by third-party CVE trackers; the
repo's own GitHub Security Advisories page showed **no advisories published directly
on `mealie-recipes/mealie`** when fetched — https://github.com/mealie-recipes/mealie/security/advisories
— these CVEs were instead disclosed via GitHub Security Lab (GHSL) and third-party CVE
databases; I did not cross-check GHSA IDs directly, only CVE IDs)

| CVE | CWE | Component | Affected | Fixed in | Summary |
|---|---|---|---|---|---|
| CVE-2024-31991 | CWE-918 SSRF | `safe_scrape_html` (recipe import) | < 1.4.0 | **1.4.0** | User-controlled recipe-import URL issued unrestricted requests; used to fingerprint internal HTTP servers via differing error responses. |
| CVE-2024-31992 | DoS (no rate limit) | `safe_scrape_html` (recipe import) | < 1.4.0 | **1.4.0** | Same import path had a timeout but no rate limiting; repeated/large requests could exhaust container CPU. |
| CVE-2024-31993 | CWE-918 SSRF | `scrape_image` (recipe image import) | < 1.4.0 | **1.4.0** | Image-import endpoint fetched a user-supplied URL without validating it was external; combined with backup/export, could exfiltrate fetched content. |
| CVE-2024-31994 | DoS | `scrape_image` (recipe image import) | < 1.4.0 | **1.4.0** | Same image-import path lacked timeout/chunking; large files could exhaust memory. |
| CVE-2026-94028 | CWE-918 SSRF | **Recipe Action Trigger** (`mealie/routes/households/controller_group_recipe_actions.py`) — NOT the plain recipe-URL scraper | 3.25.0–3.25.1 | **3.26.0** | Recipe Action Trigger POSTed to an arbitrary user-configured URL using plain `requests.post()`, bypassing the `AsyncSafeTransport` guard entirely (a different code path than `create/url`/`test-scrape-url`). Fixed by routing it through `httpx.AsyncClient` + `AsyncSafeTransport` like the rest of the app. |
| CVE-2026-71210 | CWE-367 TOCTOU (DNS rebinding) | `AsyncSafeTransport` itself (`mealie/pkgs/safehttp/transport.py`) — **directly affects `/api/recipes/create/url`, `/api/recipes/test-scrape-url`, and `/api/recipes/{slug}/image`** | Reported as "all versions" as of publication (2026-08-05) | **UNVERIFIED — see below** | The guard resolved a hostname once, validated that IP, then let the underlying transport re-resolve the hostname for the actual connection — a DNS-rebinding attacker could pass validation with a public IP then serve a private/metadata IP for the real request. |

### CVE-2026-71210 fix status — the one item I could not close out cleanly

- Third-party trackers I fetched (OpenCVE, strix.ai) both stated, as of their last
  update, that **no patched version was listed** for CVE-2026-71210 despite the
  2026-08-05 publication date.
- However, the **current `mealie-next` source I fetched directly already implements
  per-hop re-validation and appears to pin the validated IP** (consistent with the
  fix this CVE calls for), and I found a **merged** PR,
  https://github.com/mealie-recipes/mealie/pull/8376 ("fix: pin all resolved
  addresses in one curl RESOLVE entry so IPv4 fallback works"), whose description
  explicitly says "The SSRF / DNS-rebinding protection is unchanged: every address is
  still validated before pinning, and curl still can't re-resolve" — i.e. it's a
  *refinement* of an already-existing pinning mechanism, not the introduction of one.
  That strongly suggests the core DNS-rebinding fix for CVE-2026-71210 **landed in
  some release prior to `mealie-next`'s current state** (i.e., at or before
  `v3.28.0`), but **I could not confirm the exact version number where the base pin
  was first introduced** in the time available — the third-party CVE trackers are
  simply stale/incomplete on this point, and I did not find the fixing PR/commit
  itself (only the later refinement PR #8376).
- **Action for us:** treat SSRF protection as present and reasonably current in
  `v3.28.0`, but **do not rely on it as your only defense.** Recommendations for our
  client/deployment regardless of Mealie's own guard:
  1. Don't run Mealie with network access to cloud metadata endpoints or other
     internal-only services if avoidable (container network policy / firewall rule
     blocking 169.254.169.254 and RFC1918 ranges from the Mealie container, outside of
     what it needs to reach Postgres/Redis).
  2. If our sync client itself ever accepts a user-supplied URL to hand to Mealie's
     `create/url` (e.g., a Telegram bot forwarding a URL a household member pasted),
     that's still "an authenticated user causing Mealie's server to fetch a URL" —
     exactly the trust boundary all five CVEs above sit on. The API-token bearer
     alone does not change that; Mealie's own guard is the only mitigation on that
     path, and per CVE-2026-71210 that guard has had at least one confirmed bypass
     class. Don't build additional logic on the assumption that Mealie will always
     safely refuse to fetch an internal URL.

### Update 2026-09-26 (mealie lane, verified against tagged source)
- The DNS-rebinding fix (resolve once, validate, pin via curl `RESOLVE`) **first shipped in
  v3.26.0** (released 2026-09-14), from PR mealie-recipes/mealie#7914 ("fix: harden
  server-initiated HTTP against SSRF and DNS rebinding", merged 2026-09-04). Confirmed by grepping
  `mealie/pkgs/safehttp/transport.py` at tags v3.25.0 (absent), v3.26.0, v3.27.0, v3.28.0 (present).
- The CVE id CVE-2026-71210 above comes only from third-party trackers; it doesn't appear in
  Mealie's repo, release notes or that PR. Cite the PR, not the CVE id.
- `GET /api/organizers/tags/slug/{slug}` returns **500, not 404**, for an unknown slug (the handler
  returns `repo.get_one(...)` = None against `response_model=RecipeTagResponse`). Resolve a tag by
  paging `GET /api/organizers/tags` (items `{id, name, slug, recipeCount}`) instead.
- Re-importing a URL creates a copy named "Name (1)", "Name (2)"… (`RepositoryRecipes.create`),
  and after 10 collisions it returns 400.
