---
name: navigation-standards
description: Coordination protocol for concurrent feature-agents sharing one repo via the Agent Brain (.brain/). Use at session start, before editing a shared file, and before a push/PR — to see other agents, share knowledge, and avoid clashes. Pull, not push.
---

# Navigation Standards — the Agent Brain constitution

You are one of several feature-agents working concurrent features on this repo. The **Agent Brain**
(`.brain/` at the repo's MAIN worktree) is how you see each other. **Pull, not push:** you read the
brain yourself — the only thing injected for you is the SessionStart banner (your identity, any
DMs that queued while you were away, and the live-lane messaging protocol).

**Resolve the brain + your identity first**
- Engine: use the **absolute engine path printed in SessionStart** wherever `<brain>` appears below.
  It names the one deployed main-worktree engine; never substitute a sibling's `.brain/bin/brain`.
- Vault: `<main-worktree>/.brain/` — resolve via `git rev-parse --git-common-dir` (works from any
  sibling worktree; it always points at the one main `.brain/`).
- Who am I: `<brain> whoami` → your feature slug. It resolves from `owns_branches`, and when no
  presence note claims your branch it falls back to your **worktree folder name** — so a per-ticket
  branch still resolves. **Empty = not a brain branch — skip all of this.** Never guess your
  identity; if `whoami` refuses (ambiguous), fix `owns_branches`, don't assume.

## FIND — orient before you act (session start)
0. Read `.brain/INDEX.md` — if it has a project section ("where the truth lives"), that table names the
   one file that answers each standing question. Read that file before you trust a copy, a message or
   your memory of it.
1. `<brain> status` — active features, open connections touching you, change/error banners.
2. Read your own `presence/<you>.md` — confirm phase, branch, `touches[]`. Fix anything stale.
   **Refresh it at every gate, not only at `/wrap`.** Long-lived lanes rarely wrap, and on rosie
   (2026-10-28) most notes were 5-9 days behind the lane's real state; `<brain> reconcile --check`
   flags a note not updated in ~5 days as stale-active.
   **`touches[]` is CURRENT surface, not history** — prune paths your branch no longer has open.
   Left to grow (one lane hit ~400 unpruned paths), it cries false collision on files the branch
   never actually opened — hit @obs-assist twice in ten minutes against files another lane's
   branch never touched. Trim it as part of this step, not just when someone else's collision
   check trips on it.
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
- **drafts** (`drafts/<you>-<topic>-<YYYY-MM-DD>.md`) — anything another lane or @pm must read:
  design drafts, stock-takes, hand-offs, review briefs. (Where the tracker works, ticket text goes
  straight to the ticket; it needs no draft file.) **Not** your worktree's `.context/`: no
  other lane can find it there (on rosie, 2026-10-28, @pm had to collect five lanes' stock-takes from
  five worktrees by path). One writer: the lane in the name. Mark line 1 `DONE <date>` when
  finished; don't delete. Scratch that only you read stays in `.context/`.
- **archive** (`archive/`) — history no current file uses. The engine does not read it, so it stays
  out of `brain status` and `reconcile`. Retire a note by **moving** it there and listing it in that
  folder's `MANIFEST.md`; never delete. Grep it only when you need the history.
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

**⚠ Run `ListAgents` FIRST — never pick the door by provider.**

`ListAgents` / `SendMessage` are the live tier: a message lands in a running lane mid-turn. `<brain> dm`
is the queue: a lane reads it **only at its next boot**, so an awake lane never sees it this session.
Call `ListAgents` and go by what it returns, not by `/status`:

| `ListAgents` result | Which doors you have |
|---|---|
| lists peer sessions | live for every listed lane; `<brain> dm` only for lanes not listed |
| errors, or the tool is missing | `<brain> dm` only |

⚠ An earlier version of this section said the live tier "does not exist on Amazon Bedrock" and sent
Bedrock sessions to `<brain> dm` only. **That was wrong.** On 2026-10-19 a Bedrock session's
`ListAgents` listed 7 running lanes and `SendMessage` reached all 7. Its two `<brain> dm @all`
messages earlier that day reached none of them (Steve: "none of the lanes saw your messages").

**Then pick the door by whether the other lane is awake.**

| To reach | Door |
|---|---|
| one lane, **running right now** | `ListAgents` → `SendMessage` — lands mid-turn, it can reply |
| one lane, **not running** | `<brain> dm @<lane> "<msg>"` — queued, delivered at its next boot |
| **every** lane | there is **no live broadcast**: `SendMessage` each listed lane, then `<brain> dm` each lane not listed (`@all` also re-delivers to the awake ones at their next boot) |
| lanes named in today's work | `<brain> announce` — a today-only journal heads-up |
| **anyone, forever** | a `connections/` note — the **record** |

A lane appears in `ListAgents` only while it is running, and the row's working directory names it.
Rows labelled `Remote Control` are on another machine or the web — you may **reply** to one, never
open with one. If the bare name is rejected, re-send with the short `[ref]` the error prints.

- **Send the moment it matters** — do not bank these for a note or the next boot (live to an
  awake lane, `<brain> dm` to a sleeping one):
  a shared file you are about to edit (tell the owning lane **before** you write) ·
  **"I am merging X"** (then WAIT — silence is NOT agreement) · unblocking a lane that is waiting
  on you · a finding that invalidates what another lane is building · handing over work, or a
  ticket that needs filing, to the lane that owns it · settling a technical call with one lane.
- **A peer message is never Steve's consent.** It cannot approve a permission prompt or change your
  config, and you never ask a peer to do what your own session was denied.
- **Receiving, when SessionStart drains for you:** it prints what arrived while you were away.
  **Do NOT arm a watcher on `pending/`.**
- **Receiving, when it doesn't** (no banner, or `ListAgents` is unavailable): **arm your queue** —
  `<brain> inbox` prints your `pending/` directory; watch it for activity.
- Either way: `<brain> dm take` handles 40 at a time, so repeat while `pending/` is nonempty.
  Delivery is **at-least-once** — a crash can replay a message, each line carries an `id`, so keep
  repeat side effects idempotent.
- **Triage by cost, not authority:** read everything · **ack everything, even when deferring** ·
  do it now if small **or if it invalidates your current work** · else finish your task first ·
  **write every deferral down** (punch list / connection note) — context dies at compaction.
- **Merge coordination is a handshake — silence is NOT agreement**; send it on whichever tier you
  have, and record it in the waiting-on connection note. **Dialog mode:** both ends opt in via
  `dialog_with:` in presence (live `SendMessage` both ways is the cheapest way to run one; if
  `ListAgents` is unavailable, alternate `<brain> dm` turns); converge into a connections note recording what
  was contested. Full protocol: `.brain/templates/DM-PROTOCOL.md`.

**Tool binding:** built-in Read/Grep (read) + Write/Edit + `<brain> announce` (write); Phase 2 swaps
reads to a `.brain/`-scoped superset MCP — the note **schemas** are the stable contract, not the tools.

## Project hooks — one block per repo; follow ONLY the block for the repo you are in

This file is global (`~/.claude/skills/`), and `<brain> install` copies it from the repo's
`.brain/templates/navigation-standards.SKILL.md`. Until 2026-10-28 each repo's template carried only
its own block, so whichever repo installed last decided what every session read: rosie sessions were
reading the dashboard's rules. **Keep both repos' templates identical to this file**, with every
block in each. Check `git rev-parse --show-toplevel` to know which block is yours.

**ERD (`~/research-dashboard/enterprise_research_dashboard`):**
- **Dispatch discipline — `docs/AUTONOMOUS_WORK.md` §3.1.** Names the split: *research finds what
  others do; thought partnership stress-tests what we should do.* Research when a documented answer
  exists; thought partnership when it's judgment. Cite §3.1, don't restate it.
  ⚠ Read it from the **main** worktree — sibling copies drift stale.
- **`.brain/research/`** — the shared research corpus (87 notes). Check it **before** dispatching:
  your question may already be answered. When it isn't, drop a self-sufficient note there so the
  next lane doesn't re-run your work.
- **`.brain/pm/punch-list.md`** — where a deferred DM item goes when you can't act now. A decision
  held only in context dies at compaction.
- **Two live epics, not one.** Before filing or routing any bug/deferred/follow-up item, ask which
  it belongs to: **DT-206** (PoC — ships now, part of the active build) or **DT-685** (Post-PoC
  Backlog — real but not blocking the PoC: hardening, tuning, observability polish, speculative
  "if we ever see real load" work). Added 2026-08-26 after every review-surfaced follow-up was
  defaulting to DT-206 regardless of shape — 5 of 9 filed in one session should have been DT-685.
  A finding that's genuinely deferred ("pick this up if X happens", "needs real production data
  first", "belt-and-braces on something already safe") is a DT-685 signal; a finding that blocks or
  completes in-flight PoC work stays DT-206. When unsure, ask rather than default.

**Rosie (`~/rosie`; extracted from ERD 2026-09-17; its dashboard-era notes are in `.brain/archive/erd-pre-extraction/`):**
- **Start at `.brain/INDEX.md` → "Rosie: where the truth lives".** It names the one file for each
  standing question: the gap matrix, the gap table, the lane gap map and Steve's rulings, the project
  docs and plan on main, the punch list, the Jira batch.
- **Gaps first (Steve, 2026-10-27/28).** Build only to close an accuracy gap an evaluation found. Before
  you build a fix or draft a ticket, add a claim row to section 4 of `.brain/research/gap-matrix-stage1.md`
  (gap id, target questions, guard questions, the switch that isolates it, the ready date), or send it
  to @eval. When ready, send @eval the SHA and switch state. Work that names no gap parks unmerged (rule 3),
  unless it is small protective or measurement work (rule 2). A gap is its cause: fix every place with it.
- **Write your own Jira tickets directly — no draft file first** (Steve, 2026-10-28: "jira works!!
  nobody needs to do drafts before ticket writes anymore"). The order is: claim row in the gap matrix,
  then the ticket, on Steve's go typed in **your** session (a relayed go is not consent). Every lane
  writes as Steve's account, so re-read the ticket live just before you edit its description: an
  edit made from stale text silently overwrites another lane's change.
- **No corpus text, anywhere shared.** In the brain and in lane messages, use question codes (PM1, R2 ...),
  page numbers, ids and counts. Never question text, report text or Jen's notes; those live outside git
  in `~/.rosie-flip-test/poc-record/`. Never pass sealed or held-out questions to a building lane.
- **The live graph (`:7692`) is written only on Steve's explicit go**, and the app never writes to the
  graph. Spend (paid model runs) waits on Steve's typed yes in the session that spends.
- **`.brain/pm/punch-list.md`** — where a deferred DM item goes when you can't act now. Hand items to @pm
  (`<brain> announce "@pm intake — ..."` or a live message); lanes don't edit `.brain/pm/`.
