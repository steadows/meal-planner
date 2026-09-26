---
type: presence
agent: contracts
feature: "Shared contracts: config, db schema + migrations, pydantic contracts, claude_runner, fakes"
status: active
phase: "Lane 0 (contracts), supporting Phase 3 / Lane B: PR #24 (confirm_stocked joins the Pantry Protocol, no behaviour change) ALL GATES DONE, waiting on Steve's merge (CI green, ultra READY, verify READY, 17:31 EDT). Next: F2 joint PR with pantry 2b (contracts fake half on feat/contracts-f2-ask-date, cut from main after #24 merges; pantry stacks + opens it)"
owns_branches: ["contracts"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-contracts
current_branch: feat/contracts-confirm-stocked
current_ticket: none
touches: [meals/contracts.py, meals/fakes/pantry.py, tests/test_contracts.py, docs/PLAN.md]
updated: 2026-09-26T21:05:00Z
---
Lane 0 — everything else builds on this. Deliver the database schema from PLAN.md (Pantry rules → Table schema), the pydantic models (WeekProposal, RecipeOption, Intent, CartList, CartReport), `claude_runner.run()` wrapping `claude -p` / `claude --chrome -p` with timeouts and JSON validation, and a fake for every contract in `meals/fakes/`. Done when fakes pass and one real `claude -p` call returns valid JSON. Merge first; then set status: done to unblock [[contracts-pantry-waiting-on]], [[contracts-mealie-waiting-on]], [[contracts-search-waiting-on]], [[bot-contracts-waiting-on]] and [[cart-contracts-waiting-on]]. Sole owner of the contracts rule: [[bot-cart-contracts-mealie-pantry-search-wiring-shared-rule]].

## Plan decisions that change this lane's scope (Steve, via [[pm]], 2026-09-25)
Read PLAN.md → Shared contracts, Definition of done, and Rules (updated on `feat/pm`) before starting.
- `contracts.py` also defines two `typing.Protocol` interfaces: `mealie_client` (`import_url`, `get_recipe`, `list_by_tag`, `set_meal_plan`) and `pantry` (`staples_due`, status flips). The fakes in `meals/fakes/` implement them.
- `meals/fakes/` is now under the contracts-only lock alongside the four files.
- Add an `import-linter` config (layers: entry points `bot`/`jobs`/`mcp_tools`/`__main__` > feature modules > `contracts`/`db`/`config`/`claude_runner`, plus independence between feature modules; `rollup` is allowed as pure math). Check the same-layer `|` syntax against the installed version.
- `pyproject.toml` and `tests/conftest.py` are add-only for other lanes, so lay them out to make appending easy.
- **Approved by Steve 2026-09-25:** `claude_runner` always strips ANTHROPIC_API_KEY and fails loudly without /login. New domain terms accepted as-is: `Ingredient`, `CartItem`, `PantryItem`, `ClaudeRunnerError`, `MealieClient`, `Pantry`. `.context/` is git-ignored on main (PR #2).
- **Approved by Steve 2026-09-25 (second batch):** `Components` (fixed slots proteins/grains/veg/sauces/fresh) and `Substitution` (wanted/used). **CI is a go.** Before opening the PR, add ruff and mypy (pydantic plugin) to the dev group, commit `uv.lock`, and pass `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest` under Python 3.11. The CI workflow lands from [[pm]] right after this lane's first PR merges.

## Backlog from pantry PR #25 (via [[pm]], 2026-09-26)
- A `claude_runner` **"pantry-run" shape** (MCP pantry tools only; no web, Chrome or file tools). The bot lane will ask for it when it mounts the tools.
- Pre-existing: `pantry_item.typical_interval_days` has **no upper CHECK** in `db.py`, so a hand-edited huge value overflows the date maths. Your call: add a bound in a future migration, or leave it to the pantry read-side validation.
