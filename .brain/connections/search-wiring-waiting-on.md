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
updated: 2026-09-26T04:30:22Z
---
[[wiring]] builds on search and the planner from [[search]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before search. Resolves automatically when search's presence status is `done`.

**Interface agreed live, 2026-09-26 ([[search]] ↔ [[wiring]]):**
- `planner.propose(week_start, custody, *, mode="mix", recent, pantry, mealie) -> WeekProposal`: a pure function, no DB. `recent` = the last ≤3 weeks in approved/cart_filled/ordered, newest first, read by plan_state (`weekly_plan.components` round-trips a WeekProposal in every status).
- `search.find(request) -> tuple[RecipeOption, ...]`: ValueError / ClaudeRunnerError both become the one-line error reply.
- "save N" = `mealie.import_url(option.url)` at the bot (ValueError = "couldn't save that one"). "add N to this week" is composed at bot/jobs via plan_state; search never touches weekly_plan.
- **Follow-up owed by [[search]]:** `planner.apply_reply(proposal, intents) -> WeekProposal` (narrowing: recipe_options = picks, components after swaps minus what picks cover, pantry_questions = ()). Waits on [[bot-contracts-intent-typed-args]]. Separate PR after Lane E's first.
