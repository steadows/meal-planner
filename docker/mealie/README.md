# Mealie

The recipe library and the week's plan on the phone (PLAN.md, Phase 1). The meal planner talks to
it through `meals/mealie_client.py`.

## First run

1. From the repo root: `docker compose -f docker/mealie/compose.yaml up -d`. Set `PUID`/`PGID` to
   your user and group ids first (`id -u`, `id -g`) if the default 1000 doesn't match.
2. Open http://localhost:9925 and log in as `changeme@example.com` / `MyPassword`. Change the
   password right away.
3. Create a long-lived API token at `/user/profile/api-tokens`. Put it in the repo's `.env` as
   `MEALIE_TOKEN`, and set `MEALIE_URL=http://localhost:9925`.
4. After Tailscale (Phase 2), start with `MEALIE_BASE_URL=http://<tailscale-hostname>:9925` so
   links Mealie generates point at the right host.

Check the client against it: `uv run pytest -m integration tests/test_mealie_client.py`. Those
tests skip when `MEALIE_TOKEN` is unset.

## Tag conventions

The planner reads recipes by tag, so the tag slugs are part of the contract between lanes.
Rename one only together with the lanes that read it.

| Tag | Meaning | Read by |
| --- | --- | --- |
| `protein`, `grain`, `veg-tray`, `sauce` | The Sunday-cook component slots | planner |
| `kid-cook` | A cook-with-Miles recipe (Wednesday) | planner |
| `lunch-build` | One of Steve's lunch builds | planner |
| `rotation` | A favorite the Saturday planner may offer again | planner (`list_by_tag("rotation")`) |
| `batch-ok` | Batches and reheats well. Sets `RecipeOption.batch_ok`; untagged reads as "not marked" | `get_recipe` |

Imports never bring in the source site's own tags (`includeTags: false`), so these stay the only
tags in play.

## How the client uses Mealie

- **Import:** `POST /api/recipes/create/url`. Mealie fetches the page server-side through its own
  SSRF guard, and the client rejects local and private URLs before sending them.
- **Recipes:** ingredients a URL import leaves as plain text are split into quantity, unit and food
  by Mealie's parser (`/api/parser/ingredients`) when read. Nothing is written back to the recipe.
- **Meal plan:** each planned recipe is a dinner entry on the cook day (the Sunday), marked
  "Planned by meal-planner". Re-planning replaces only those entries. Anything added by hand stays.
