# Adversarial review brief — Lane C (Mealie client), pre-PR

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Your job is to find issues a thorough first-pass review MISSED,
not to repeat what it already found. Findings only. Do not edit code or files.

## Data boundary (Steve, 2026-09-25)
You may read `meals/`, `tests/`, `docs/`, `docker/`, `pyproject.toml`, `.github/` and the
`.context/seams/` design note. Do NOT read `.env`, anything under `data/` or `seed/`, or any real
Telegram, Mealie or Meijer content. You may read Mealie's public source on GitHub
(`github.com/mealie-recipes/mealie`, tag `v3.28.0`) to check API behaviour.

## Review the PINNED commit, not the working tree
The author is editing the working tree while you run. Review exactly this range:
`git diff origin/main...98447428b5232d9105c03a332c40d659ce76d5a5`, and read files with
`git show 98447428b5232d9105c03a332c40d659ce76d5a5:<path>`.
Files: `meals/mealie_client.py` (read in full), `tests/test_mealie_client.py`,
`tests/test_mealie_client_integration.py`, `docker/mealie/compose.yaml`, `docker/mealie/README.md`,
`pyproject.toml`.

Context (pre-existing, so it's the standard the diff is measured against): `meals/contracts.py`
(the `MealieClient` Protocol and `RecipeOption`), `meals/config.py`, `docs/PLAN.md`
(Implementation plan, Runtime concurrency, Risks), `CLAUDE.md`, `docs/AUTONOMOUS_WORK.md`.
Design by this diff's author: `.context/seams/lane-c-mealie.md`. Shared rule:
`/Users/stevemeadows/meal-planner/.brain/connections/mealie-search-shared-rule.md`.

What the code does: a sync httpx client for Mealie v3.28.0 implementing the `MealieClient`
Protocol. `import_url` guards against SSRF with a pure, no-DNS host check (Mealie's own
fetch-time transport is the primary control), slugs and tags are allowlisted before going into
URL paths, `get_recipe` parses text-only ingredients via Mealie's NLP endpoint,
`set_meal_plan` replaces only entries carrying a marker text, and `from_settings` wires the
bearer token.

## Deference rule
The dividing line for trusting a comment, docstring or design-doc section is not code vs docs.
It's authored-by-this-diff vs pre-existing. Anything the diff adds or changes (including the
seam map and all docstrings in `meals/mealie_client.py`) is a claim by the author, and must be
checked against the code before you clear a finding on its authority. Anything that predates the
diff (contracts.py, PLAN.md, CLAUDE.md) is the standard the code is measured against. Where they
disagree, the pre-existing contract wins unless the diff explicitly and defensibly amends it.
Don't generalise this to "discount documentation".

## Not a false positive
Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
Mealie behaviour not yet exercised) is not a false positive just because the trigger hasn't
occurred yet. If the code as written produces the wrong behaviour once that condition is real,
it's a live finding now.

## What the first-pass review already caught (SKIP these; they're being fixed)
- HIGH `meals/mealie_client.py:158-164`: `list_by_tag` treats 404 as an unknown tag, but Mealie
  v3.28.0's `GET /api/organizers/tags/slug/{slug}` returns None → 500 for an unknown slug, so it
  raises instead of returning ().
- MEDIUM `meals/mealie_client.py:129-139`: a slow scrape past the 60 s timeout raises
  httpx.ReadTimeout, not the Protocol's ValueError, and Mealie may still finish (orphan recipe).
- MEDIUM `meals/mealie_client.py:37`: `_SCRAPE_FAILURES` maps 422 (client-body bug) and 500
  (server fault) to the user-facing ValueError "couldn't import".
- MEDIUM `meals/mealie_client.py:251`: `not ip.is_global` lets reserved IPv6 forms through
  (`::7f00:1`, `::ffff:0:7f00:1`, `64:ff9b::a9fe:a9fe`). Fix: add `is_reserved`.
- MEDIUM `meals/mealie_client.py:288-292`: `_servings` falls back to `recipeYieldQuantity`, which is
  the yield (cookies, loaves), not servings.
- MEDIUM `meals/mealie_client.py:283-285`: `_source` → `urlsplit(org_url)` raises ValueError on a
  malformed orgURL, crashing `get_recipe`.
- MEDIUM `meals/mealie_client.py` (whole file): no logging, while the repo convention is
  `logging.getLogger(__name__)`. SSRF refusals and plan mutations leave no trace.
- LOW `meals/mealie_client.py:179-185`: duplicate marker entries for one recipe are never
  collapsed.
- LOW `meals/mealie_client.py:253`: multi-label local names (`.local`, `.internal`, `.home.arpa`)
  pass the client guard, and the README overclaims "rejects local and private URLs".
- LOW `docker/mealie/compose.yaml:12`: `"9925:9000"` binds 0.0.0.0 while Mealie ships a default
  admin password.
- LOW `meals/mealie_client.py:50`: `_Tag.id` is required, but Mealie's `RecipeTag.id` is nullable.
- LOW `meals/mealie_client.py:166-170`: `list_by_tag` re-pages /api/recipes although the tag
  response embeds `recipes`.
- LOW rollback: data written into Mealie isn't undone by reverting the code.
Also considered and dismissed (don't re-raise without new evidence): delete-before-create
ordering in `set_meal_plan`, no duplicate-import detection (a documented deferral), re-parsing
ingredients on every read (a documented design choice), `httpx.Client` never closed (one
client per process), and the v3.26.0 vs v3.28.0 attribution in the compose comment.

## Your mandate
1. SKIP anything already covered above. No duplicates.
2. Focus on blind spots: subtle logic errors under specific input combinations; cross-file
   interaction bugs (contracts.py Protocol semantics vs this implementation, the fake in
   `meals/fakes/mealie.py` vs real behaviour, which downstream lanes test against); security in
   "normal" code (trust boundaries: slugs from Claude output, recipe content from the web,
   token handling); error-handling gaps where failures cascade silently; pydantic model /
   Mealie schema mismatches that would break reads of real recipes; tests that pass against
   the stub but would pass against a wrong implementation too, or that encode Mealie
   behaviour Mealie doesn't have.
3. For each finding give: **File**, **Line**, **Category** (security | correctness |
   concurrency | compatibility | cascade-failure | state-machine), **Severity** (CRITICAL |
   HIGH | MEDIUM), **Finding**, **Evidence** (why it's real; cite Mealie source or code), and
   **Fix**.
4. If you can't find material issues beyond the list above, say so explicitly. Don't invent
   findings.
