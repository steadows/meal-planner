---
type: connection
features: [search, wiring]
kind: waiting-on
status: open
severity: medium
blocks: [wiring]
files: [meals/search.py, meals/planner.py, meals/prefs.yaml, meals/prompts/search/, meals/prompts/planner/, tests/test_search.py, tests/test_planner.py]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-09-26T05:45:53Z
---
[[wiring]] builds on search and the planner from [[search]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before search. Resolves automatically when search's presence status is `done`.

**Interface agreed live, 2026-09-26 ([[search]] ↔ [[wiring]]):**
- `planner.propose(week_start, custody, *, mode="mix", recent, pantry, mealie) -> WeekProposal`: a pure function, no DB. `recent` = the last ≤3 weeks in approved/cart_filled/ordered, newest first, read by plan_state (`weekly_plan.components` round-trips a WeekProposal in every status).
- `search.find(request) -> tuple[RecipeOption, ...]`: ValueError / ClaudeRunnerError both become the one-line error reply.
- "save N" = `mealie.import_url(option.url)` at the bot (ValueError = "couldn't save that one"). "add N to this week" is composed at bot/jobs via plan_state; search never touches weekly_plan.
- **Follow-up owed by [[search]]:** `planner.apply_reply(proposal, intents) -> WeekProposal` (narrowing: recipe_options = picks, components after swaps minus what picks cover, pantry_questions = ()). Waits on [[bot-contracts-intent-typed-args]]. Separate PR after Lane E's first.
- **`RecipeOption.mealie_slug` (agreed live 2026-09-26 by [[contracts]], [[mealie]], [[wiring]] and [[search]]; the contracts PR still needs Steve's OK because it changes PLAN's contracts table):** `SkipJsonSchema[str | None] = None`, filled by `get_recipe`. Pick rule: `option.mealie_slug or mealie.import_url(option.url)`, never URL matching and never re-importing a favorite. **Trust boundary (revised by [[contracts]]):** the guard lives in contracts, in the same PR as the field. Structured Claude output is validated with `context={"untrusted": True}`, and a non-None `mealie_slug` is rejected, which becomes the normal ClaudeRunnerError. That covers search, planner and any future Claude consumer, and [[search]] owes no follow-up for it. **No consumer that trusts `mealie_slug` ships before that PR is on main.**
- **mealie_slug is now default-deny ([[contracts]], 2026-09-26):** a non-None slug validates only under `context={contracts.TRUSTED: True}`. Rule: slugs come only from `get_recipe()`, and TRUSTED is never used on anything Claude produced. [[search]] owes a three-line test-only fix (test_planner.py:333/345/363 should compare against `fake_mealie.get_recipe(FAVORITE)`) once the contracts PR is up, whichever of that PR and #10 merges second.
