# Adversarial review brief — bot PR 2a: chat gate, `/pantry`, `run_bot` (`meals/bot/`)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Find the issues a thorough first-pass review MISSED; do not
repeat what it already found. Findings only: do not edit any file.

## Boundaries

- Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, the `.context/reviews/`
  files named below, plus installed python-telegram-bot under
  `.venv/lib/python3.11/site-packages/telegram/` for behaviour checks. Never read `.env` (a symlink
  to real secrets), anything under `data/` or `seed/`, or any real Telegram, Mealie or Meijer
  content.
- You do not need git: the diff is pre-written at `.context/reviews/pr2a.diff`, and every reviewed
  source file is new (`meals/bot/__init__.py`, `gate.py`, `handlers.py`; `tests/test_bot/*`). You
  may try `.venv/bin/python -m pytest tests/test_bot -q` or a small `.venv/bin/python -c ...` probe.
  If a command is denied or unavailable, carry on with a static review. Cite file:line for every
  claim.

## Deference rule

What matters for trusting a comment, docstring or design doc is whether this diff authored it or it
was already there. Anything the diff adds is the author's claim: verify it against the code before
clearing a finding on its authority. Anything that predates the diff is the standard.
- Pre-existing: `meals/contracts.py` (the `Pantry` Protocol), `meals/pantry.py`, `meals/db.py`,
  `meals/background.py`, `meals/config.py`, `docs/PLAN.md`, `docs/adr/ADR-0001-runtime-model.md`
  (Ownership → bot: the chat-ID allowlist in one PTB TypeHandler, group -1, raising
  ApplicationHandlerStop), `CLAUDE.md`.
- Authored by this PR: everything under `meals/bot/` and `tests/test_bot/`, and the contract below.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the trigger hasn't happened yet. If the
code as written does the wrong thing once that condition is real, it is a live finding now.

## The done contract — what this change promised

# Done contract — bot PR 2a (PLAN Phase 4 / Lane D, first slice)

Story: the meal-planner Telegram bot. PR 2a is the first of several PRs; the ACs below are the
ones this PR proves. Other story ACs (free text, update_id claim, /due, /plan, voice, /find
follow-ups) are proven by later PRs and show `not run` here.

## Done means (this PR)
Only Steve's chat (TELEGRAM_ALLOWED_CHAT_ID) is served; `/pantry` answers from the live pantry;
`run_bot()` refuses to start without its settings — and every AC below is proven.

## Acceptance criteria

| AC | Given / When / Then | Proof |
|---|---|---|
| AC-1 chat lock | Given an update from any chat other than TELEGRAM_ALLOWED_CHAT_ID, or with no chat, when it arrives, then no later handler runs, nothing is read or written, nothing is replied | `tests/test_bot/test_gate.py::test_foreign_chat_stops_every_handler`, `::test_update_without_chat_is_dropped` (positive control `::test_allowed_chat_reaches_later_groups`; registration `::test_app_is_sequential_and_rate_limited`, `::test_gate_is_blocking_in_group_minus_one`) |
| AC-13 /pantry (this PR: /pantry only) | `/pantry` replies exactly two lines, `On the list: <names>` (status buy_next_time) and `Have: <names>` (status have), names in `list_items()` order joined with ", ", `nothing` for an empty group; an empty pantry replies `The pantry is empty. Run the seed loader first.`; long text is split with `background.split_message`; every reply is plain text; a failed read replies one line `Couldn't read the pantry: ` + `background.first_line(exc)`, cut to `background.ERROR_MAX_CHARS` | `tests/test_bot/test_commands.py::*` |
| AC-16 startup refusal | Given settings with no TELEGRAM_BOT_TOKEN, or no TELEGRAM_ALLOWED_CHAT_ID, or a PANTRY_DB path that doesn't exist, when `run_bot()` is called, then it refuses before polling, names what's missing, never shows the token, and creates no database file; otherwise it polls without dropping pending updates and serves /pantry from the configured PANTRY_DB to the configured chat only | `tests/test_bot/test_run_bot.py::*` |

## Out of scope (findings here are DROP)
- `python -m meals bot` subcommand, bot logging setup, launchd plist → the wiring lane (P4.1)
- The update_id replay claim, the gate's fail-closed wrapper, free text, `/due`, `/plan`, edited messages → bot PR 2b
- Voice → bot PR 3; `/find`, save N, add N → bot PR 4
- prefs.yaml edits, one-off items, ratings, the Saturday reply path → later phases / rulings

## Budget
PRs: this one (2a) of 2a/2b/3/4. Review stop rule: stop when two consecutive post-PR single-agent
Codex convergence rounds find no product defect. Later list: empty at start.

### Acceptance → proof (from the fresh-context done check)

Every row passes: AC-1 (5 checks), AC-13 (8 checks), AC-16 (6 checks), 26 tests in
`.context/reviews/pr2a-tests.txt`; the full suite is 1135 passed; ruff, mypy --strict and
import-linter clean. No unproven AC.

## Bin every finding

## 2. The three-bin finding rule (every review gate, every round)

Every finding from `/simplify`, `/steadows-code-review`, the Codex sweep, `/steadows-ultrareview`,
`/steadows-verify` or a seams pass gets exactly one bin:

| Bin | When | What happens |
|---|---|---|
| **FIX NOW** | It breaks an AC or the team DoD (§3), or it is a correctness/security/data-loss defect in code this story adds or changes | Fixed in this PR, or in a follow-on PR **under the same story**. The story stays open until it is fixed |
| **DROP** | A nit, style, or speculative future concern; OR pre-existing and outside this story's surface; OR it sits in the story's Out of scope list | One line in the PR summary. **No ticket** |
| **LATER** | A real defect that can't be fixed here, for exactly one reason: `post-PoC` · `other-owner` (another owner's infra or lane) · `needs-ruling` (a Steve product call) · `needs-design` (ARCHITECTURE-first) | One line on the story's **Later list**, with its reason. **No ticket from the lane** |

**Severity doesn't pick the bin; scope does.** A HIGH on this story's surface is FIX NOW. A HIGH outside it
that is pre-existing is LATER, tagged `poc-blocking`.

"It would need its own RED suite" or "the PR is getting big" is **not** a LATER reason. It means a follow-on
PR under the same story (Steve: fix it, don't enumerate it; keep PRs small).

A finding that lands in the Out of scope list is DROP: one line, no argument.

## What the first-pass review already caught (do not repeat; a DIFFERENT bug on the same line is fair game)

All scored 50 by verification, so severity LOW:
- LOW `meals/bot/handlers.py:103-106` — `run_bot` checks PANTRY_DB once at startup; each `/pantry` then calls `get_db(path)`, which re-creates and migrates an empty DB if the file vanished mid-run.
- LOW `meals/bot/handlers.py:70` — `/pantry` runs the full `get_db` (WAL switch retry, migrate with BEGIN IMMEDIATE) on the event loop; with a pending migration and a held write lock it can block the loop ~5 s.
- LOW `meals/bot/handlers.py:100` — the startup check is only `is_file()`: a non-SQLite or newer-schema file passes startup and every `/pantry` then fails.
- LOW `meals/bot/handlers.py:60` — `CommandHandler("pantry")` also matches `edited_message` by default (out of scope: edited messages → PR 2b).
- LOW `meals/bot/handlers.py:79-80` — `reply_text` errors unhandled and no app error handler: a send failure mid-chunks drops the rest; the error goes to PTB's logger, not `meals.*`.
- LOW `meals/bot/handlers.py:103` — altitude: the per-call `get_db` is the common cause of the first three items.
- LOW `meals/bot/handlers.py:79` — the split-and-send loop duplicates private `background._reply`.
- LOW `meals/bot/handlers.py:74` — the error line duplicates private `background._one_line` with another prefix.
- LOW `meals/bot/handlers.py:97` — `TELEGRAM_ALLOWED_CHAT_ID=0` passes startup; the bot then drops every update.
- LOW `meals/bot/gate.py:18` — a refused chat is logged with only the chat id (no user id, no update type).

Already known and routed elsewhere: httpx logs request URLs (which contain the bot token) at INFO;
the bot doesn't configure logging (wiring P4.1 owns that and has been told).

## Your mandate

1. Skip anything already covered above.
2. Focus on what automated reviewers miss: subtle logic errors under specific inputs; cross-file
   interactions (PTB's dispatch, `background.py`, `db.py`, `config.py`); trust boundaries (the chat
   gate is the bot's only access control — think hard about update types that carry a chat the gate
   might mis-read, e.g. channel posts, business messages, callback queries, inline queries, chat
   member updates, `effective_chat` vs `message.chat`); concurrency or ordering assumptions;
   error handling that fails silently; what happens when `run_bot` is later called by
   `python -m meals bot` under launchd KeepAlive.
3. For each finding give: **File**, **Line**, **Category** (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), **Severity** (CRITICAL | HIGH | MEDIUM),
   **Bin** (FIX NOW | DROP | LATER + reason), **Finding**, **Evidence**, **Fix**.
4. If the first pass was thorough and you find nothing material, say so explicitly. Do not invent
   findings.
