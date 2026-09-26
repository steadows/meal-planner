---
type: connection
features: [wiring, contracts, cart]
kind: shared-file
status: resolved
severity: high
blocks: []
files: [meals/db.py, meals/claude_runner.py]
discovered: 2026-09-26T03:56:07Z
resolved: 2026-09-26T20:53:03Z
updated: 2026-09-26T20:53:03Z
---
**Asks from the runtime-model ADR ([[wiring]], launchd-based per Steve) to [[contracts]]. Tracked by [[pm]].**

1. **`job_run` table → migration 2, after #6 merges.** `db.MIGRATIONS` is append-only, so it's not folded into migration 1. [[wiring]] sends the migration as a small PR to [[contracts]], or sends the shape and contracts adds it, once #6 is on main.
2. **Crash-orphan double-cart hole (severity high).** If `cart_fill` crashes while its Chrome `claude` child keeps running, the job lock frees, and "retry cart" could start a second fill, doubling the cart. [[contracts]] flagged it; the fix may need a small runner API change (`hold_fds`: the child keeps holding the job lock's fd until it exits). [[wiring]]'s ADR decides whether it wants that, then asks [[contracts]] for the API change. [[cart]] must know about this before building: a cart fill is never safe to start while any previous fill's process is alive.

Neither blocks #6.

**Migration sequencing ([[pm]], 2026-09-26 ~01:00): two lanes are asking [[contracts]] for schema changes.** `db.MIGRATIONS` is append-only, so contracts assigns numbers in the order the PRs merge. Don't pre-claim "migration 2" in either lane's code.
- [[pantry]]: `pantry_item.next_ask_on` (the explicit "ask again on" date for "still good / have plenty"). **Not gated**; it can go first.
- [[wiring]]: the `job_run` table. **Gated on Steve confirming ADR-0001.**
If both land in one contracts PR, fine. Otherwise whichever merges first is 2.

**Resolved ([[contracts]], 2026-09-26):** both asks are on main in **#21** (8c24816, Steve's go).
- `job_run` is migration **3** (the ADR DDL verbatim).
- `claude_runner.run(..., hold_fds=())` passes the fds to every claude child, with `_check_hold_fds` refusing fds below 3 or not open before a slot is taken.
- The rules for [[wiring]] and [[cart]] are in `run()`'s docstring:
  - flock locks only;
  - release by closing the fd, never LOCK_UN;
  - the lock lasts while claude, or anything it started, has the fd;
  - `started_at` is UTC 'YYYY-MM-DD HH:MM:SS'.
- Still open, and not contracts': wiring's P4.4 lifecycle test and `plan_state`, and cart's `fill(cart_list, hold_fds)`.

