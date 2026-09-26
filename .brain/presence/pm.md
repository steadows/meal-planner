---
type: presence
agent: pm
feature: "PM/coordination lane: sweeps the vault, unblocks waiting-on edges, tracks merge order against PLAN.md, files follow-ups — owns no meals/ code"
status: active
phase: machine setup done — raise open items with Steve, then hand off to contracts
owns_branches: ["pm"]
plan: docs/PLAN.md (Concurrency lanes, Coordination with agent-brain)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-pm
current_branch: feat/pm
current_ticket: none
touches: [.brain/journal/2026-09-25.md, .brain/presence/bot.md, .brain/presence/cart.md, .brain/presence/contracts.md, .brain/presence/mealie.md, .brain/presence/pantry.md, .brain/presence/search.md, .brain/presence/wiring.md]
updated: 2026-09-26T01:13:36Z
---
Coordination lane, not a build lane — owns no files under `meals/`. Job: periodic sweep of `.brain/` (presence notes, `connections/`, journal) to catch stalled lanes, unresolved `waiting-on` edges, and merge-order violations against the diagram in PLAN.md (`Concurrency lanes`); message a stuck lane; keep this note's `status` honest. Comms: live `SendMessage` between lanes (Steve, 2026-09-25) — do NOT arm a watcher on `dm/pm/pending`.

## ▶ PICK UP HERE — next session (written 2026-09-25, session 1)

Steve is starting the next session with permissions bypassed so pm can finish the setup the auto-mode classifier blocked ("Unauthorized Persistence" on writing `~/.zshenv` / global git config). **Nothing from that step was applied.**

### Done in session 1
- Four familiarization sweeps: CLAUDE.md/docs, PLAN.md, environment, brain.
- Created 7 lane worktrees, each on `feat/<lane>` tracking `origin/feat/<lane>`, all at `ccbeb47` (= main): `/Users/stevemeadows/meal-planner-{contracts,pantry,mealie,search,bot,cart,wiring}`. `brain whoami` verified correct from each.
- Rewrote each lane presence note's `current_worktree` from the dead `/Users/amap3i/meal-planner` to its real worktree path.
- Steve logged in as `steadows` twice: once plain `gh auth login` (default `~/.config/gh`; this made steadows the GLOBAL active gh account), and once into `GH_CONFIG_DIR=~/.config/gh-steadows` (the correct per-repo slot).

### 1. ✅ DONE 2026-09-25 (session 2) — steadows scoping applied and verified
All five steps applied. The includeIf key uses the expanded `$HOME` form (`gitdir:/Users/stevemeadows/meal-planner/`) so the Undo line in docs/BOOTSTRAP.md matches verbatim. Verified from a clean `env -i` zsh: `gh` resolves to the wrapper; gh + credential fill = steadows in both main and the contracts worktree; gh in `~` = AMAP3I_MLKN; credential fill outside the repo still = steadows-mlkn (untouched, as intended); `push --dry-run` succeeds from contracts and pm; `gh repo view` = steadows/meal-planner ADMIN. Original steps kept below for reference.

Pre-checked on 2026-09-25: none of these files existed, `~/.gitconfig` had no includeIf, `~/.local/bin` already exists (holds uv, neo4j-mcp, etc.), real gh = `/opt/homebrew/bin/gh`, and a clean non-interactive zsh has neither `~/.local/bin` nor homebrew on PATH. Re-check before writing.
1. `~/.gitconfig-steadows`: `[user] name = Steve Meadows, email = 56893550+steadows@users.noreply.github.com`; `[ghauth] configdir = /Users/stevemeadows/.config/gh-steadows`; `[credential "https://github.com"]` with `helper =` (empty, resets the Xcode osxkeychain helper), then `helper = !GH_CONFIG_DIR=/Users/stevemeadows/.config/gh-steadows /opt/homebrew/bin/gh auth git-credential`.
2. `git config --global --add 'includeIf.gitdir:~/meal-planner/.path' '~/.gitconfig-steadows'`. This covers every worktree, because their gitdirs live under `~/meal-planner/.git/worktrees/`.
3. `~/.local/bin/gh` (chmod +x): `dir=$(git config --get ghauth.configdir 2>/dev/null) && export GH_CONFIG_DIR="$dir"; exec /opt/homebrew/bin/gh "$@"`.
4. Append `export PATH="$HOME/.local/bin:$PATH"  # meal-planner bootstrap` to `~/.zshenv`.
5. Restore global gh: `env -u GH_CONFIG_DIR /opt/homebrew/bin/gh auth switch --hostname github.com --user AMAP3I_MLKN`. Leave the steadows entry in `~/.config/gh` inactive. Do NOT `gh auth logout` it: both config dirs share the keyring token, so logging out would kill the gh-steadows slot too.
6. **Verify:**
   - Run `(cd ~/meal-planner && gh auth status && git config user.email)`; expect steadows.
   - Run `(cd ~ && gh auth status)`; expect AMAP3I_MLKN.
   - `printf 'protocol=https\nhost=github.com\n\n' | git -C ~/meal-planner-contracts credential fill` should return username steadows, not steadows-mlkn.
   - `git -C ~/meal-planner-contracts push --dry-run origin feat/contracts` should succeed.
   - Test from a lane worktree, not only the main checkout.

### 2. Open items to raise with Steve after setup
- **`.claude/settings.json` (tracked):** it allowlists `/Users/amap3i/meal-planner/.brain`, which is dead here, so every brain call prompts for permission. The engine's own comment says this file should be gitignored and local. Needs a small PR; Steve decides.
- **Machinery bug, to file with `brain propose machinery`:**
  - `reconcile` (brain ~L1129-1136) rewrites the booting lane's `touches` from `git -C $ROOT diff origin/main`. That diffs the MAIN checkout, not the lane's worktree, so the first boot wipes each lane's hand-seeded touches.
  - The push/PR collision check (~L1607) diffs `$ROOT` the same way.
  - The collision check also flags a lane's own connections.
- **Uncommitted brain state in the main checkout:** 7 presence path fixes, this note (untracked), and the journal (duplicate "pm joined" line). No committer is named anywhere; decide who runs `brain commit` from `~/meal-planner`. `feat/pm` has no upstream on origin.
- **Stale-downgrade clock:** open waiting-on edges drop to `watch` once a blocker's `updated` is more than 5 days old (around 2026-09-30). Get contracts moving, or bump it, before then.
- **Plan gaps to settle before contracts starts:**
  - F→G merge order (PLAN L611) isn't a brain edge.
  - Contracts-only scope: 1 file per PLAN, 4 files per CLAUDE.md; `fakes/` is covered by neither.
  - The import-only-contracts rule (PLAN L555) conflicts with planner needing pantry and mealie_client.
  - Prompt location: "next to module" (PLAN L557) vs `meals/prompts/<lane>/` (CLAUDE.md).
- **Env not built yet:** no Mealie container (port 9925), no `.env`, no pyproject. Python 3.11+ is available via `uv`.
- **Installed skill:** the global `~/.claude/skills/navigation-standards` is the ERD-customized copy. It references `docs/AUTONOMOUS_WORK.md`, `.brain/pm/punch-list.md` and "87 research notes", none of which exist here, and its whoami folder-name fallback isn't in this engine. `.brain/research/` doesn't exist yet.
