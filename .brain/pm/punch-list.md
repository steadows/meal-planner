# Punch list — meal-planner open loose ends

_@pm-maintained. Disposition: **OPEN · ESCALATED** (waits on Steve) **· DONE · WONTFIX**. Lanes hand items in with
`brain announce "@pm intake — ..."` at `/wrap`; @pm records them here. This repo has no Jira._

## Seeded 2026-11-01 from the pm handoff of 2026-09-28

- **OPEN** — The ADR-0001 fitness suite (`tests/architecture/`) is missing; `/steadows-verify` warned. Owner TBD.
- **OPEN** — Meijer boundary gap: nothing stops in-session navigation off meijer.com at runtime (only `--tools ""`
  plus a validated report URL). For the cart spike and the cart-path architecture gate (Lane F).
- **OPEN** — The pantry MCP mount rule (no web, Chrome or file tools in the pantry run) is convention-only until
  contracts adds a `claude_runner` pantry-run shape (`connections/bot-contracts-pantry-wiring-shared-rule`).
  Needed when bot wires the pantry run.
- **OPEN** — Wiring P4 asks: the "retry cart" 6-day edge fix; a `cart.fill` hook for the 20-minute Chrome-lock alarm
  (D9); the pantry lookup for the under-$35 staple suggestion (D27).
- **OPEN** — Contracts backlog: a `typical_interval_days` CHECK; `deploy` in the mypy `files` list.
- **OPEN** — `uv run pytest -m integration` calls the real `claude -p` in two search tests until search adds the
  `live_claude` gate (search's next PR).
- **OPEN** — `CLAUDE_LOCK_DIR` is missing from `.env.example`.
- **OPEN** — Two scratch dirs that could not be deleted: pantry's witness dir and wiring's `scratchpad/venv311`.
- **OPEN** — Harness tooling (Steve's `~/.claude`, not this repo): read-only tools for review and audit subagents;
  test-writer's Bash-written scratch files bypass `enforce-test-scope.py`; verify subagent naming. Route each to
  the shared list `~/.claude/hooks/lint-candidates-harness.md` (bump Seen if a row already covers it).
- **OPEN** — ADR-0001 deferred follow-ups (not v1): a `python -m meals doctor` command (env, claude probe, launchd
  state, lock holders); an outside heartbeat or uptime alert for "the Mac is down".
- **OPEN** — Mealie deferred items: duplicate-import detection (a re-import makes "Name (1)"); plan-entry ownership
  through a dedicated Mealie bot user; the image is pinned by tag, not digest; double tag I/O (LOW).
- **ESCALATED** — Optional: set macOS updates to download but not auto-install (ADR-0001 risk #26: with FileVault,
  nothing runs after a reboot until Steve logs in).
- **ESCALATED** — CLAUDE.md's entry-point list lacks `seed_loader`. pm doesn't edit CLAUDE.md on a peer's request.
- **ESCALATED** — PLAN doc bumps: the Mealie pin v3.27.0 → v3.28.0; the Phase 2 phone-path step.
- **ESCALATED** — Tailscale phone access needs Steve's sign-in once.
- **ESCALATED** — The pantry seed CSV header (in #12's body) needs Steve's confirmation before the Phase 0 export.
- **ESCALATED** — Product calls that already have working defaults in code. Ask one at a time:
  - the Saturday nudge window (keep Sat 16–21, pm's pick);
  - wiring P2: the first-week custody guess, the busy message only on `--retry`, no auto-retry after a failed
    approval, "retry cart" refuses weeks older than 6 days, the two ADR gaps (a Mealie pick that never imports; no
    slug write-back);
  - search: which Sunday `wed+sat_sun` means; who writes `prefs.yaml` at runtime, and where it lives.
