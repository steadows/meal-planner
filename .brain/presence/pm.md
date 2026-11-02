---
type: presence
agent: pm
feature: "PM/coordination lane: sweeps the vault, unblocks waiting-on edges, tracks merge order against PLAN.md, files follow-ups — owns no meals/ code"
status: active
phase: "Resumed 2026-11-01 after a 5-week pause. Brain brought to the rosie/dashboard spec (engine = dashboard 6d8885c4a; .brain/pm/ and drafts/ added); gardener port on feat/pm-gardener. Next: brief bot and search on arrival; Steve's still-good floor call unblocks contracts + pantry F2"
owns_branches: ["pm"]
plan: docs/PLAN.md (Concurrency lanes, Coordination with agent-brain)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pm
current_branch: feat/pm-gardener
current_ticket: none
touches: [.claude/skills/gardener/SKILL.md, .claude/skills/gardener/harvest_findings.py, .claude/skills/gardener/ratchets.py]
updated: 2026-11-02T04:39:40Z
---
Coordination lane, not a build lane: owns no files under `meals/`. Job: sweep `.brain/` (presence notes,
`connections/`, journal) for stalled lanes, open `waiting-on` edges and merge-order slips against PLAN.md
(Concurrency lanes); brief and unblock lanes; bring Steve one decision at a time; run `/gardener`. Comms: live
`SendMessage` between lanes (Steve, 2026-09-25); no watcher on `dm/pm/pending`.

**Now (2026-11-01):**
- Brain to spec: engine and nav-skill template updated, `.brain/pm/` (README, punch-list, lint-candidates,
  gardener-log) and `.brain/drafts/` created. `/wrap` Phase 5b and 6b now run in this repo.
- Gardener port (`.claude/skills/gardener/`) on `feat/pm-gardener`; PR next, merge on Steve's go.
- Lanes restarting: bot (Phase 4 / Lane D, never started) and search (Lane E follow-ups). pm briefs each on arrival.
- Waiting on Steve: the "still good" one-week floor (blocks contracts `feat/contracts-f2-ask-date` @ 3abb7f9 and
  pantry `feat/pantry-f2-ask-date` @ de616df); then a go for mealie's `MealieUnavailable` wrap.
- Not yet: cart (needs the Meijer test in Steve's Chrome), wiring P4 (needs the other lanes).

**Pointers:** open loose ends live in `.brain/pm/punch-list.md`. Last handoff:
`~/meal-planner-pm/.context/handoffs/2026-09-26-pm-lane.md`.
