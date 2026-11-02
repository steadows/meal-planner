# Lint Candidates — meal-planner's code (lessons waiting to become rules)

> Owned by @pm (the gardener). Any lane appends rows at `/wrap` Phase 6b. Never delete a row: bump **Seen** when a
> fault class recurs, and set **Status** when it lands or is rejected. The gardener (`/gardener`) lands one queued row
> per run as a small, separate PR. No row needs Steve's approval.
>
> **Which list?** This file holds rules for **meal-planner's own code** (`meals/`, `tests/`, `deploy/`, CI). Anything
> whose fix lives in the shared harness (hooks, Codex dispatch, shell and git habits, the brain engine) goes in the
> shared list `~/.claude/hooks/lint-candidates-harness.md`, so a slip in any project bumps one row.
> **Fault-class names:** use the shared catalog at the top of that file, so the same mistake gets the same name in
> every list. Add a new class there first.
>
> **Pick-up order:** highest Seen × lowest Effort first. **Ratchet, don't big-bang:** a new rule starts at today's
> violation count (allowlist or per-file-ignores) so it never stalls in-flight work, and the count can only go down.
>
> Started 2026-11-01 (Steve: make sure `/wrap` includes the lint bumps; meal-planner had no list, so Phase 6b never
> ran here).

## Queue

| # | Class | Source | Mechanism (layer) | Ratchet cost today | Seen | Effort | Status |
|---|---|---|---|---|---|---|---|

## Log

- 2026-11-01 (@pm): file started with an empty queue. Header from rosie's list, with meal-planner's code scope.
