# Agent Brain — Index

> Coordination vault for the concurrent feature-agents on this repo. It lets multiple AI
> orchestrators see each other, share knowledge, and surface cross-feature gotchas.
>
> **Agents:** run the **navigation-standards** skill at session start, then query the brain
> yourself with built-in Grep/Read — *pull, not push*.
> **Humans:** run `.brain/bin/brain status` for the live dashboard.

## Map

- **presence/** — one note per *feature*: where each agent is (status, phase, branch, touches).
- **connections/** — the cross-feature blackboard: `clash` · `shared-file` · `waiting-on` · `shared-rule`.
- **journal/** — append-only daily comms timeline (`brain announce` writes here).
- **dm/** — per-lane DM inboxes + `read/` archives (`brain dm` writes here). **Per-machine and
  gitignored** — unlike every folder above it, this is transient and never committed.
- **research/** — shared, self-sufficient findings (the durable home for research).
- **CHANGES.md** — structural-change log (governance gauntlet).

## Live state

Run **`.brain/bin/brain status`** — active features, open connections touching you, and any
brain-change / hook-error banners. (No Dataview in v1; `brain status` is the single live source.)

## How identity works

Each presence note declares `owns_branches`. An agent's feature = the branch's *positional slug*
(the token after the ticket id in `feat/<ticket>-<slug>-…`, or the whole branch name otherwise)
matched exactly against `owns_branches`. `brain whoami` resolves it; empty = not a brain branch.
