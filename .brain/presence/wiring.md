---
type: presence
agent: wiring
feature: "Scheduled jobs and end-to-end: sat_propose, sat_nudge, sun_autoapprove, cart_fill, entry point"
status: active
phase: ADR-0001 runtime model ACCEPTED + merged (PR #9). Next: P1 /steadows-seams → meals/background.py (unblocks bot). P0 contracts gated PR (job_run, hold_fds, layers) queued after #13.
owns_branches: ["wiring"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-wiring
current_branch: feat/wiring
current_ticket: none
touches: [.brain/journal/2026-09-26.md]
updated: 2026-09-26T15:41:41Z
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
