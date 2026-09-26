---
type: presence
agent: search
feature: "Recipe search (/find) and the Saturday planner: prefs profile, prompts, WeekProposal"
status: active
phase: RED+GREEN search.find, then planner.propose (seam map .context/seams/search-lane-e.md; tier ordinary)
owns_branches: ["search"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-search
current_branch: feat/search
current_ticket: none
touches: [meals/search.py, meals/planner.py, meals/prefs.yaml, meals/prompts/search/, meals/prompts/planner/, tests/test_search.py, tests/test_planner.py]
updated: 2026-09-26T04:30:22Z
---
Lane E. `search.find(request)` returns 3-5 RecipeOptions filtered by `prefs.yaml` (seed it from PLAN.md → Preferences profile). `planner.propose(week)` returns a WeekProposal in the chosen mode (mix default, recipes, components). Uses the Mealie fake until [[mealie]] lands. Waits on [[contracts-search-waiting-on]]. Unblocks [[search-wiring-waiting-on]].

## Heads-up from [[contracts]] (via [[pm]], 2026-09-25): what `claude_runner` gives you
The child Claude gets web tools only (`--tools WebSearch,WebFetch`), runs in an empty temp dir, with `--safe-mode` and an allowlisted env (no ANTHROPIC_API_KEY). Verified live: structured output via `--json-schema` works, about 3K tokens of context per call, and an injected recipe page can't read `.env`. Needing more tools is a [[contracts]] PR plus a security look, not a call-site parameter. Field descriptions on the pydantic models flow into the JSON schema Claude sees, so use them to steer output. Example: pantry_questions = "item names, not question text".
