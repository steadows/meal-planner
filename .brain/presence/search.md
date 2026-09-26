---
type: presence
agent: search
feature: "Recipe search (/find) and the Saturday planner: prefs profile, prompts, WeekProposal"
status: done
phase: DONE — PR #10 merged to main (a26be74, 2026-09-26). Follow-ups: planner.apply_reply (waits on bot typed Intent args), fallback pantry question (waits on Pantry.list_items)
owns_branches: ["search"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-search
current_branch: feat/search
current_ticket: none
touches: [.brain/connections/bot-runtime-design-waiting-on.md, .brain/connections/mealie-wiring-waiting-on.md, .brain/journal/2026-09-26.md, .brain/presence/search.md, .brain/presence/wiring.md]
updated: 2026-09-26T14:22:36Z
---
Lane E. `search.find(request)` returns 3-5 RecipeOptions filtered by `prefs.yaml` (seed it from PLAN.md → Preferences profile). `planner.propose(week)` returns a WeekProposal in the chosen mode (mix default, recipes, components). Uses the Mealie fake until [[mealie]] lands. Waits on [[contracts-search-waiting-on]]. Unblocks [[search-wiring-waiting-on]].

## Heads-up from [[contracts]] (via [[pm]], 2026-09-25): what `claude_runner` gives you
The child Claude gets web tools only (`--tools WebSearch,WebFetch`), runs in an empty temp dir, with `--safe-mode` and an allowlisted env (no ANTHROPIC_API_KEY). Verified live: structured output via `--json-schema` works, about 3K tokens of context per call, and an injected recipe page can't read `.env`. Needing more tools is a [[contracts]] PR plus a security look, not a call-site parameter. Field descriptions on the pydantic models flow into the JSON schema Claude sees, so use them to steer output. Example: pantry_questions = "item names, not question text".

## Follow-ups after #10 merged (via [[pm]], 2026-09-26)
- `planner.apply_reply(proposal, intents)` follow-up PR, once [[bot]]'s typed Intent args land.
- Add one line to search's seams: "`mealie_slug` comes only from `get_recipe`; never pass TRUSTED on Claude output" (from [[wiring]]).
- #10 is merged, so set `status: done` if this lane has no active PR (that resolves `search-wiring-waiting-on`). Reopen with a new phase when the follow-up starts.
