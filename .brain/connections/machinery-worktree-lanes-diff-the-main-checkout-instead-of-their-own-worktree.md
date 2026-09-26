---
type: connection
features: []
kind: machinery
status: watch
severity: medium
blocks: []
files: []
discovered: 2026-09-26T01:22:32Z
resolved: null
updated: 2026-09-26T01:22:32Z
---
MACHINERY change proposed (human-applied in v1): worktree lanes diff the main checkout instead of their own worktree
Agents keep working with current machinery until a human applies this deliberately.

## Details (filed by [[pm]], 2026-09-25, verified against bin/brain)

`ROOT` is `brain_root` (the main checkout, `~/meal-planner`, on `main`). Lanes run in sibling worktrees (`~/meal-planner-<lane>`, on `feat/<lane>`). Three places use `ROOT` where they mean the lane's own worktree:

1. **`cmd_reconcile` auto-fix (~L1130-1136)** rewrites the booting lane's `touches` from `git -C "$ROOT" diff --name-only <main_ref>`. That's the main checkout's diff, i.e. whatever uncommitted `.brain/` files sit there, so the first reconcile from a lane wipes its hand-seeded `touches`. Evidence: `presence/pm.md` `touches` is now a list of `.brain/presence/*.md` + journal files, not pm's real surface.
2. **`_hook_pre_tool` push/PR collision check (~L1607)** diffs `$ROOT` the same way on `git push` / `gh pr create`, so it checks the main checkout's changes, not the branch being pushed.
3. **`_detect_collisions` (~L1630-1645)** skips the lane's own presence note but not connections that list the lane in `features:`. Example: contracts pushing `meals/contracts.py` gets flagged against the shared-rule note it owns.

Suggested fix: diff `$(_toplevel)` (already defined, L58) instead of `$ROOT` in 1 and 2; in 3, skip a connection whose `features` includes `$_me` when it's a shared-rule the lane owns (or any connection naming `$_me`).

4. **Related UX trap (hit by [[contracts]], 2026-09-26):** each lane worktree carries a *tracked* copy of `.brain/`, which looks editable but is never read, since the brain resolves `$ROOT` to the main checkout. Contracts edited its worktree copy all session, and `brain status` showed it idle. Mitigated in CLAUDE.md (PR #5). A machinery fix could warn when the worktree's `.brain/presence/<me>.md` differs from `$ROOT`'s.
