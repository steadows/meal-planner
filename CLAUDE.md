# CLAUDE.md — meal-planner

Read this, then `docs/PLAN.md` (the sections named below), before writing code.

## Orientation

- The design is in `docs/PLAN.md`. The sections you need most: **Implementation plan** (code layout, shared contracts, definition of done) and **Concurrency lanes** (who owns what, what waits on what).
- This repo is coordinated with agent-brain (`.brain/`). Follow the `navigation-standards` skill at session start. Your lane is resolved from your branch: `feat/<lane>`.
- Your presence note is `.brain/presence/<lane>.md`. Keep `status`, `phase` and `touches` honest. Set `status: done` when your lane has merged — that is what unblocks the lanes waiting on you.

## Lane rules

- Work only in the files your lane owns (listed in `touches` in your presence note). Anything else: create or update a `connections/` note first.
- Only the `contracts` lane edits `meals/contracts.py`, `meals/db.py`, `meals/config.py` and `meals/claude_runner.py`. Other lanes request changes with `brain dm @contracts` and a small PR.
- Test against `meals/fakes/`. Real services (Mealie, Telegram, Chrome/Meijer, `claude -p`) only in integration tests marked `@pytest.mark.integration`.
- Rebase on `main` at the start of every session.

## Code conventions

- Python 3.11+. Type hints everywhere. Pydantic models for everything that crosses a module boundary.
- Claude prompts live in `.md` files under `meals/prompts/<lane>/`, not in inline strings.
- Never commit secrets. Configuration comes from `.env` via `meals/config.py`.
- Never let the cart lane open non-meijer.com URLs in the logged-in Chrome session (prompt-injection boundary — see PLAN.md, Risks).
- Claude Code in Chrome cannot and must not complete a purchase. The cart lane stops at a filled cart.

## Git

- GitHub account for this repo: `steadows` (scoped per folder; do not switch the global gh account).
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
- PR per lane into `main`. Announce with `brain announce "about to PR"` before opening it.
