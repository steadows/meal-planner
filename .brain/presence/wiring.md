---
type: presence
agent: wiring
feature: "Scheduled jobs and end-to-end: sat_propose, sat_nudge, sun_autoapprove, cart_fill, entry point"
status: active
phase: "Phase 5 / Lane G, ADR P2 2.1–2.7 MERGED (PR #26 = c0e41f8, 2026-09-27T00:19Z, on Steve's go). PLAN Phase 5 boxes 1, 3, 4 [~] (#26). Next: P3 launchd deploy on feat/wiring-p3, held until Steve says go. P1 merged earlier (#20)."
owns_branches: ["wiring"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-wiring
current_branch: feat/wiring
current_ticket: none
touches: [.brain/connections/bot-contracts-pantry-confirm-stocked.md, .brain/journal/2026-09-26.md, .brain/presence/contracts.md, .brain/presence/pantry.md, .brain/presence/wiring.md, architecture-plan.html, docs/PLAN.md, docs/prompts/pantry-pr3-adversarial-review.md, docs/prompts/pantry-pr3-ultrareview.md, docs/prompts/wiring-p2-jobs-adversarial-review.md, docs/prompts/wiring-p2-jobs-convergence.md, docs/prompts/wiring-p2-jobs-ultrareview.md, meals/__main__.py, meals/background.py, meals/contracts.py, meals/fakes/pantry.py, meals/jobs.py, meals/mcp_tools.py, meals/plan_state.py, pyproject.toml, tests/test_contracts.py, tests/test_jobs_cart.py, tests/test_jobs_send.py, tests/test_jobs_weekend.py, tests/test_main.py, tests/test_mcp_tools.py, tests/test_plan_state.py, uv.lock]
updated: 2026-09-27T00:48:17Z
---
Lane G — last. Jobs keyed on weekly_plan.status so every job is safe to rerun; one Chrome session at a time via a lock; bot never blocks (background subprocesses); max two concurrent claude processes. Done when a full Saturday dry run works end to end (M4). Waits on [[pantry-wiring-waiting-on]], [[mealie-wiring-waiting-on]], [[search-wiring-waiting-on]] and [[bot-wiring-waiting-on]].

## ▶ First session: run the runtime-model architecture gate NOW (Steve, via [[pm]], 2026-09-25)
Design only, no code, so it doesn't wait on your code dependencies. Run `/steadows-architect` on the **runtime model**: the bot process, background runs that reply when done (the bot needs this for `/find` on day one), the Saturday schedule (cron vs launchd vs the agentic-OS scheduler), the one-Chrome-at-a-time queue, and safe reruns after a restart.
- **Inputs:** PLAN.md → Runtime concurrency, Where the weekly planning job runs, Architecture gates.
- **Fixed, don't redesign:** `claude_runner`'s process limit, which is cross-process and owned by [[contracts]]. It's not on main yet; read `~/meal-planner-contracts/.context/seams/contracts-lane0.md` read-only, and ask [[contracts]] live.
- **Ownership:** who builds the background-run piece (bot or wiring) is a decision for the ADR; raise it as a Stage 2 question.
- Steve confirms the ADR. Once it's on main, resolve [[bot-runtime-design-waiting-on]].

## Trap for `plan_state` (from [[mealie]], via [[pm]], 2026-09-26)
A stored `WeekProposal` read back from `weekly_plan.components` must be validated with `context={contracts.TRUSTED: True}`. Real `get_recipe` options now carry `mealie_slug`, which is **default-deny** without that context (contracts.py:33-37). This is by design; it's only a trap if you forget the context. Pin it with a round-trip test that includes a slug.

## P2 notes from [[contracts]] (ADR-gated PR, 2026-09-26)
- Job and Chrome locks must be `fcntl.flock`. fcntl/lockf record locks aren't inherited, so `hold_fds` can't carry them to claude.
- Release a held lock by **closing the fd**, never with `flock(fd, LOCK_UN)`. That unlocks the shared open file description for claude and its descendants too (`_slot` avoids it for this reason).
- A lock stays held while claude **or anything it started** keeps the fd, so bear that in mind for the stale-lock alert.
- `job_run.started_at` is SQLite `CURRENT_TIMESTAMP`, UTC text `'YYYY-MM-DD HH:MM:SS'`. Compare it, and write any `--retry` reset, in that form, not in local time or isoformat with a `T`.
- Write `outcome` and `finished_at` in one statement. Nothing in the DDL ties them together; a CHECK would need an ADR amendment plus a migration.
- `run()` will raise ValueError for a hold fd of 0-2, a negative fd, or one that isn't open, before taking a slot.

## For P4.4 (from [[contracts]] #21, via [[pm]], 2026-09-26)
`hold_fds` inheritance by claude's **own descendants** is tested only one level deep (claude → one grandchild). If P4's integration or acceptance tests rely on a deeper process tree holding the lock, add that case there.
