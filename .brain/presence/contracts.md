---
type: presence
agent: contracts
feature: "Shared contracts: config, db schema + migrations, pydantic contracts, claude_runner, fakes"
status: idle
phase: "Lane 0 (contracts), supporting Phase 3 / Lane B: #24 (confirm_stocked on the Pantry Protocol) MERGED 2026-09-26 as c289c5b; base feat/contracts = main c289c5b. Next session: F2 joint PR. Contracts cuts feat/contracts-f2-ask-date from main with the fake half (plus Steve's plenty floor, datetime coercion, _find strip), then pantry stacks 2b on it and opens the single PR"
owns_branches: ["contracts"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-contracts
current_branch: feat/contracts
current_ticket: none
touches: [.brain/connections/bot-contracts-pantry-confirm-stocked.md, .brain/journal/2026-09-26.md, .brain/presence/contracts.md, .brain/presence/pantry.md, .brain/presence/wiring.md, architecture-plan.html, docs/PLAN.md, docs/prompts/pantry-pr3-adversarial-review.md, docs/prompts/pantry-pr3-ultrareview.md, docs/prompts/wiring-p2-jobs-adversarial-review.md, docs/prompts/wiring-p2-jobs-convergence.md, docs/prompts/wiring-p2-jobs-ultrareview.md, meals/__main__.py, meals/background.py, meals/contracts.py, meals/fakes/pantry.py, meals/jobs.py, meals/mcp_tools.py, meals/plan_state.py, pyproject.toml, tests/test_contracts.py, tests/test_jobs_cart.py, tests/test_jobs_send.py, tests/test_jobs_weekend.py, tests/test_main.py, tests/test_mcp_tools.py, tests/test_plan_state.py, uv.lock]
updated: 2026-09-27T00:48:14Z
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
