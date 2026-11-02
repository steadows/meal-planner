# `.brain/pm/` — the pm lane's shared files

The pm lane coordinates the build lanes and owns no `meals/` code. Its shared registers live here, so
every lane and Steve can see them. The live copy is the main checkout's vault
(`~/meal-planner/.brain/pm/`). It reaches git through `brain commit`, run from the main checkout.
Worktree copies of `.brain/` are stale snapshots: never edit them.

| File | What it holds | Who writes it |
|---|---|---|
| `punch-list.md` | Open loose ends and deferred items, each with a disposition | @pm. Lanes hand items in with `brain announce "@pm intake — ..."` at `/wrap` (Phase 5b) |
| `lint-candidates.md` | Lessons waiting to become lint, CI or test rules for meal-planner's code | Any lane at `/wrap` (Phase 6b): add a row or bump **Seen**. The gardener lands them |
| `gardener-log.md` | One row per `/gardener` run: the ratchet counts and what landed | The gardener (`.claude/skills/gardener/`) |

Rules:

- Lanes don't edit `punch-list.md`. They hand items to @pm, and @pm records them.
  `lint-candidates.md` is the one file lanes append to directly.
- This repo has no Jira. A deferred (LATER) item goes on the punch list through @pm intake.
- Merges, pushes outside the brain, and anything outward-facing wait for Steve's go.

Started 2026-11-01, to the spec rosie's pm set for the dashboard and rosie repos.
