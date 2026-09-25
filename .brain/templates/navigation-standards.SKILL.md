---
name: navigation-standards
description: Coordination protocol for concurrent feature-agents sharing one repo via the Agent Brain (.brain/). Use at session start, before editing a shared file, and before a push/PR — to see other agents, share knowledge, and avoid clashes. Pull, not push.
---

# Navigation Standards — the Agent Brain constitution

You are one of several feature-agents working concurrent features on this repo. The **Agent Brain**
(`.brain/` at the repo's MAIN worktree) is how you see each other. **Pull, not push:** you read the
brain yourself — nothing is injected for you beyond a one-line session-start nudge.

**Resolve the brain + your identity first**
- Engine: use the **absolute engine path printed in SessionStart** wherever `<brain>` appears below.
  It names the one deployed main-worktree engine; never substitute a sibling's `.brain/bin/brain`.
- Vault: `<main-worktree>/.brain/` — resolve via `git rev-parse --git-common-dir` (works from any
  sibling worktree; it always points at the one main `.brain/`).
- Who am I: `<brain> whoami` → your feature slug. **Empty = not a brain branch — skip all of
  this.** Never guess your identity; if `whoami` refuses (ambiguous), fix `owns_branches`, don't assume.

## FIND — orient before you act (session start)
1. `<brain> status` — active features, open connections touching you, change/error banners.
2. Read your own `presence/<you>.md` — confirm phase, branch, `touches[]`. Fix anything stale.
3. Grep `connections/` for your slug and for each path you're about to touch.

## READ — before touching a shared file
Before you edit or create a file, check who else is in it:
- Grep other **active** `presence/*.md` for the path, and `connections/*.md` `files:`.
- A trailing `/` means a **zone** (directory prefix); anything under it coordinates. Else exact file.
- Overlap with another active feature → there's a clash. Found an existing connection → read it and
  follow it. Overlap but **no** note yet → **create one** (below) after a search, so you never dup.

## WRITE — the notes (built-in Write/Edit)
- **presence** (`presence/<you>.md`) — your live board, single-writer (only you write your note).
  Keep `status` honest: `active | blocked | idle | done`. Update `phase`, `touches`, `current_*`
  as you move ticket→ticket. `blocked_on` is **not stored** — you compute "am I blocked?" by reading
  your referenced connections' live `status`.
- **connections** (`connections/<slug>.md`) — one note per cross-feature edge. **Slug = sorted
  feature names + kind** (e.g. `cockpit-graph-clash`) so two agents detecting the same edge collapse
  to one file. Search before you create; on a name clash, read the winner and proceed — never a second
  write. Kinds (label only): `clash · shared-file · waiting-on · shared-rule`. **Never rename** (it
  orphans `[[links]]`); close by flipping `status: resolved` (kept as history).
- **research** (`research/<topic>.md`) — before researching X, check here (and grep); after, drop a
  **self-sufficient** distilled note so others skip the re-research. The brain is research's durable
  home (gitignored scratch in `docs/research/` doesn't travel across worktrees).
- **Never `git add .brain/` from a sibling worktree.** Only the agreed committer runs `<brain> commit`
  (from main). Everyone else only edits note *content* — the shared folder already propagates it.

## ANNOUNCE — the three moments (journal)
`<brain> announce "<msg>"` at:
1. **phase start** — what you're starting + the surface you'll touch.
2. **phase end / gate** — what's done and **who you just unblocked** (`@feature`, `[[link]]` them).
3. **about to PR** — the changed surface.
Journal = ephemeral heads-up; connection/presence = durable state. One moment often does both.

## EVOLVE — governance (conventions self-evolve; machinery is human-applied in v1)
- A **convention** change (a note format, or this skill): `<brain> propose convention "<desc>"` →
  `<brain> apply`. It auto-validates (`<brain> reconcile --check`), records in `CHANGES.md` (surfaced by
  `<brain> status`), and is revertible (`<brain> revert <id>`). Auto-applies even unattended.
- A **machinery** change (the hooks or the `brain` script): `<brain> propose machinery "<desc>"` files a
  proposal and you **keep working** with current machinery — a human applies machinery in v1.
- **Nothing ever blocks on the human.** Where you'd "wait on Steve," decide and proceed; record the
  call for optional later review. The human never manages coordination, keeps no lists, chases nothing.

## DM — the fast tier (signal, never the record)
- `<brain> dm @<lane> "<msg>"` → their inbox in seconds (the nudge); `<brain> dm @all` fans to every
  registered queue (status broadcast / merge coordination). `announce` is a today-only journal
  heads-up for lanes explicitly mentioned. The durable **record is a connection note**.
- **Arm your queue now:** `<brain> inbox` prints your `pending/` directory. Watch it for activity;
  `<brain> dm take` handles 40 at a time, so repeat while `pending/` is nonempty. Delivery is **at-least-once** — a crash can replay a message, each line carries an `id`, so keep repeat side effects idempotent.
- **Triage by cost, not authority:** read everything · **ack everything, even when deferring** ·
  do it now if small **or if it invalidates your current work** · else finish your task first ·
  **write every deferral down** (punch list / connection note) — context dies at compaction.
- **Merge coordination is a handshake — silence is NOT agreement**; record it in the waiting-on
  connection note. **Dialog mode:** both ends opt in via `dialog_with:` in presence; converge into
  a connections note recording what was contested. Full protocol: `.brain/templates/DM-PROTOCOL.md`.

**Tool binding:** built-in Read/Grep (read) + Write/Edit + `<brain> announce` (write); Phase 2 swaps
reads to a `.brain/`-scoped superset MCP — the note **schemas** are the stable contract, not the tools.

## Project hooks — deployed copies append project-specific referents below; the generic template stays agnostic.
