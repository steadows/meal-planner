# meal-planner

A personal meal-planning assistant. Every Saturday it proposes the week's meals (full web recipes, batch-cook components, or a mix), fills the Meijer pickup cart after one Telegram reply, and keeps the pantry honest so staples aren't bought twice.

The full design — food rules, pantry logic, architecture, dependencies, build phases and concurrency lanes — is in **[docs/PLAN.md](docs/PLAN.md)**. Start there.

## How the pieces fit

| Piece | Runs on | Job |
| --- | --- | --- |
| Mealie (Docker) | Home machine | Recipe library and the week's plan, viewed on the phone |
| `meals` package (this repo) | Home machine | Pantry, recipe search, weekly planner, Telegram bot, scheduled jobs |
| Claude Code (`claude -p`, `claude --chrome -p`) | Home machine / Chrome computer | Parsing replies, recipe search, filling the Meijer cart |
| Tailscale | All devices | Private access to Mealie and the bot from anywhere |

## Working in this repo (agents and humans)

This repo is coordinated with [agent-brain](https://github.com/steadows/agent-brain). The vault lives in `.brain/`.

- **One lane per branch, named `feat/<lane>`.** The brain resolves your lane from the branch name.
- **Start of session:** the SessionStart hook prints `brain status`. Read your presence note in `.brain/presence/<lane>.md` and any connection touching you.
- **Waiting on another lane?** Check `.brain/connections/*-waiting-on.md`. They resolve automatically when the blocking lane sets `status: done` in its presence note.
- **Only the `contracts` lane edits `meals/contracts.py`.** Other lanes ask for changes with a DM (`brain dm @contracts "..."`) and a small PR.
- **Announce** phase start, phase end, and "about to PR" with `brain announce`.

| Lane | Branch | Waits on |
| --- | --- | --- |
| contracts | `feat/contracts` | — |
| pantry | `feat/pantry` | contracts |
| mealie | `feat/mealie` | contracts |
| search | `feat/search` | contracts |
| bot | `feat/bot` | contracts, plus answers about the existing Telegram bot |
| cart | `feat/cart` | contracts, pantry |
| wiring | `feat/wiring` | pantry, mealie, search, bot |

Each lane rebases on `main` when it starts. `contracts` merges first.

## Local setup

```sh
cp .env.example .env        # fill in tokens; .env is gitignored
python3 -m venv .venv && . .venv/bin/activate
# dependencies land with the contracts lane (pyproject.toml)
```

Secrets never go in the repo. The GitHub account for this folder is `steadows`, scoped by `~/.gitconfig-steadows` (see the bootstrap notes in `docs/BOOTSTRAP.md`).
