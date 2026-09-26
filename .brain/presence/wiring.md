---
type: presence
agent: wiring
feature: "Scheduled jobs and end-to-end: sat_propose, sat_nudge, sun_autoapprove, cart_fill, entry point"
status: active
phase: "P1 meals/background.py (unblocks bot): seams, RED (test-writer + spec-watchdog), GREEN (22/22, 100% cov), and /simplify done; /steadows-code-review in progress; then PR, ultra, verify. Adds python-telegram-bot>=22.8 to pyproject. P0 contracts gated PR (job_run, hold_fds, layers) in contracts' hands."
owns_branches: ["wiring"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-wiring
current_branch: feat/wiring
current_ticket: none
touches: [meals/background.py, tests/test_background.py, pyproject.toml, uv.lock, architecture-plan.html]
updated: 2026-09-26T19:04:52Z
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
