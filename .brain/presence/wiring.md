---
type: presence
agent: wiring
feature: "Scheduled jobs and end-to-end: sat_propose, sat_nudge, sun_autoapprove, cart_fill, entry point"
status: idle
phase: waiting on pantry, mealie, search, bot
owns_branches: ["wiring"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-wiring
current_branch: feat/wiring
current_ticket: none
touches: [meals/jobs.py, meals/__main__.py, deploy/, tests/test_jobs.py, tests/test_e2e.py]
updated: 2026-09-25T14:17:50Z
---
Lane G — last. Jobs keyed on weekly_plan.status so every job is safe to rerun; one Chrome session at a time via a lock; bot never blocks (background subprocesses); max two concurrent claude processes. Done when a full Saturday dry run works end to end (M4). Waits on [[pantry-wiring-waiting-on]], [[mealie-wiring-waiting-on]], [[search-wiring-waiting-on]] and [[bot-wiring-waiting-on]].

## ▶ First session: run the runtime-model architecture gate NOW (Steve, via [[pm]], 2026-09-25)
Design only, no code, so it doesn't wait on your code dependencies. Run `/steadows-architect` on the **runtime model**: the bot process, background runs that reply when done (the bot needs this for `/find` on day one), the Saturday schedule (cron vs launchd vs the agentic-OS scheduler), the one-Chrome-at-a-time queue, and safe reruns after a restart.
- **Inputs:** PLAN.md → Runtime concurrency, Where the weekly planning job runs, Architecture gates.
- **Fixed, don't redesign:** `claude_runner`'s process limit, which is cross-process and owned by [[contracts]]. It's not on main yet; read `~/meal-planner-contracts/.context/seams/contracts-lane0.md` read-only, and ask [[contracts]] live.
- **Ownership:** who builds the background-run piece (bot or wiring) is a decision for the ADR; raise it as a Stage 2 question.
- Steve confirms the ADR. Once it's on main, resolve [[bot-runtime-design-waiting-on]].
