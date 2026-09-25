---
type: presence
agent: wiring
feature: "Scheduled jobs and end-to-end: sat_propose, sat_nudge, sun_autoapprove, cart_fill, entry point"
status: idle
phase: waiting on pantry, mealie, search, bot
owns_branches: ["wiring"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/amap3i/meal-planner
current_branch: feat/wiring
current_ticket: none
touches: [meals/jobs.py, meals/__main__.py, deploy/, tests/test_jobs.py, tests/test_e2e.py]
updated: 2026-09-25T14:17:50Z
---
Lane G — last. Jobs keyed on weekly_plan.status so every job is safe to rerun; one Chrome session at a time via a lock; bot never blocks (background subprocesses); max two concurrent claude processes. Done when a full Saturday dry run works end to end (M4). Waits on [[pantry-wiring-waiting-on]], [[mealie-wiring-waiting-on]], [[search-wiring-waiting-on]] and [[bot-wiring-waiting-on]].
