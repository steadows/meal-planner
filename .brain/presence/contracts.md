---
type: presence
agent: contracts
feature: "Shared contracts: config, db schema + migrations, pydantic contracts, claude_runner, fakes"
status: idle
phase: ready to start
owns_branches: ["contracts"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-contracts
current_branch: feat/contracts
current_ticket: none
touches: [meals/__init__.py, meals/config.py, meals/db.py, meals/contracts.py, meals/claude_runner.py, meals/fakes/, pyproject.toml, tests/conftest.py, tests/test_contracts.py]
updated: 2026-09-25T14:17:50Z
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
