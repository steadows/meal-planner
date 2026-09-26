# Post-PR ultrareview brief: wiring P1, `meals/background.py` (GitHub PR #20, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
The change set is branch `feat/wiring` against `origin/main` in this working tree. HEAD is the PR
head, including this brief's own commit, which is docs only. The PR description is at the bottom of
this file, verbatim.

- **Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a
  cache under `/tmp`. The sandbox has denied that before, and it ended an earlier review. These files
  are pre-written for you:
  - `.context/reviews/p1-diff.patch`: `git diff origin/main...HEAD` for everything in the PR
  - `.context/reviews/p1-commits.txt`: the commit list
  - `.context/reviews/p1-pr-body.md`: the PR description, also reproduced below

  If `git` works for you, fine, but don't depend on it. Using these files is the intended path, not
  a workaround. Every file named here is in the working tree at HEAD, so read it directly.
- **Don't run `uv`, `pytest` or `python` against the repo.** They write caches (`.venv`,
  `.pytest_cache`, `__pycache__`) that the sandbox may deny. The suite is already green (24/24; the
  five CI commands pass on Python 3.11, 646 passed). Reason from the code. Throwaway probes are
  allowed only under `/tmp` with the system Python and no repo imports. **A denied WRITE is a
  stop-and-report, never a workaround.**
- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. The diff is small
  in product code (`meals/background.py`, 136 lines), but it is **concurrency and process
  lifecycle**:
  - an in-flight counter shared between the event loop and executor done-callbacks
  - a deadline that deliberately does not free the slot
  - a detached child process that must outlive the bot

  Per the skill's risk override, size the fleet at the Max tier.
- **REVIEW ONLY:** do not edit, create, commit or push anything in the repo. Don't spawn nested
  sub-fleets beyond the skill's own design.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only these:
  - `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.github/`
  - `CLAUDE.md`, `.context/seams/P1.md`, `.context/reviews/`
  - the installed PTB source under `.venv/lib/python3.13/site-packages/telegram/`

  Never read `.env`, `data/` or `seed/`. Never contact Telegram, Mealie or Meijer.
- **Standards (pre-existing, authoritative):**
  - `docs/adr/ADR-0001-runtime-model.md` (accepted by Steve): Decision :43-60, Consequences
    :245-260, Ownership :266-295, Integration Test Points :440-460
  - `docs/AUTONOMOUS_WORK.md`
  - `meals/contracts.py`, `meals/claude_runner.py`

  The author's own claims (verify them, don't defer to them): the docstrings in
  `meals/background.py`, `.context/seams/P1.md` (the design contract), and `tests/test_background.py`.
- **Prior review, and dispositions not to re-raise unless the disposition is wrong:**
  - `.context/reviews/p1-code-review.md` is the full pre-PR report: kept and fixed items, LOW
    declines, discards, and the Codex sweep's two MEDIUMs, which were deliberately left to the bot
    lane (AIORateLimiter; `concurrent_updates`).
  - `docs/prompts/wiring-p1-background-adversarial-review.md` lists the first-pass findings with
    their dispositions.
- **Callers that don't exist yet** (judge the API against them):
  - the bot lane's `/find` handler will call `start`;
  - the bot (approval → `spawn_job('cart_fill', '--week', W)`) and the P2 jobs (`sun_autoapprove`,
    `reconcile`) will call `spawn_job`.

  See ADR-0001 Ownership and `.brain/connections/bot-background-py-waiting-on.md` (in the MAIN
  checkout, `/Users/stevemeadows/meal-planner/.brain/`; read-only) for the API notes handed to bot.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), category, evidence
  (cite code; probes only where cheap and sandbox-safe) and a concrete fix. Only report findings
  that survive the skill's verification. Say explicitly if nothing material is found. Also state
  which files you actually read, so it's clear the diff was really reviewed.

---

## PR #20 description (verbatim)

## What this is

This PR adds `meals/background.py`, the piece the Telegram bot needs so `/find` (and other slow chat work) doesn't freeze it. The bot replies "on it", does the work in the background, and replies with the answer when it's done. It also adds `spawn_job`, which starts a detached `python -m meals job …` process for work that has to outlive the bot, like a cart fill after Steve approves the plan.

This is ADR-0001 phase **P1**: the bot lane builds against it next. It adds `python-telegram-bot>=22.8` to the project dependencies.

## Behaviour

**`start(update, context, work, render, ack)`** returns at once, so the bot keeps taking updates. It:
- replies `ack`,
- runs `work` in a worker thread,
- replies `render(result)`.

Limits:
- **At most 2 in flight.** A third request gets "busy, try again in a few minutes", and its work never runs.
- **After 15 minutes it tells Steve it gave up,** but the slot is freed only when the worker thread really ends. Python threads can't be cancelled, and that's what keeps ADR-0001's "abandoned work is bounded by the in-flight cap" true. It follows that `work` **must have a bounded runtime** (claude_runner, httpx and SQLite all do; see the docstring). The give-up and the late finish are both logged at WARNING, so a stuck slot shows up in the log.

Replies:
- **Every reply is plain text** (`parse_mode=None`, which overrides any `Defaults`), because recipe titles and error text come from the web or from Claude.
- **Replies over 4096 characters are split** into several messages.
- **Errors come back as one line,** "Sorry, that didn't work: <first line>", and never include `ClaudeRunnerError.raw_output`.

**`spawn_job(name, *args) -> pid`** starts the child:
- in its own session,
- with stdin closed and none of the bot's fds,
- with stdout and stderr inherited, so an early crash lands in the bot's log.

It never waits for the child. `OSError` reaches the caller, who writes the message, because it depends on what was starting.

## How it was built (AUTONOMOUS_WORK.md gates)

1. **Seams:** `/steadows-seams` with tier **HIGH** (concurrency and process lifecycle).
2. **RED** (`25bec98`): `test-writer`, then one `spec-watchdog` pass. The watchdog killed 45 of 46 mutants; H1–H3 were fixed and 3 trims applied.
3. **GREEN** (`f49049e`), then `/simplify` (`0411db2`: one plain-text `_reply` helper, no redundant flag).
4. **`/steadows-code-review`:**
   - `/code-review` plus an obs/deps/rollback lens produced 16 findings. Each was confidence-scored by a read-only scorer, and 7 were kept.
   - The fixes were RED-first again (`bd79edf` tests, `d0bc110` fixes):
     - long replies are split;
     - a failed "busy" send is logged, not raised;
     - the abandoned-work end is logged at WARNING and tested;
     - there's a slot-leak check after every test.
   - The single-agent Codex sweep found 2 MEDIUMs. Both are Application-level settings for the bot lane (below), so neither is fixed here.
   - The first Codex dispatch reviewed nothing: the sandbox denied git's xcrun cache write. The brief was changed to read a pre-written diff (`32e8dfc`).

The adversarial brief is in `docs/prompts/wiring-p1-background-adversarial-review.md`.

## Left to the bot lane, on purpose

These are the Codex sweep's findings. Both are settings for the whole bot, not for this file:
1. **Flood control.** Chunks are sent back to back, so a `RetryAfter` partway through would drop the rest. PTB's standard fix is `AIORateLimiter` (the `python-telegram-bot[rate-limiter]` extra) on the Application, which paces and retries every send.
2. **Sequential updates.** PTB handles updates one at a time by default, so any in-handler send (including the "busy" reply) delays the next update while Telegram is slow. That's the Application's `concurrent_updates`.

Both are written up in `.brain/connections/bot-background-py-waiting-on.md`, with the rest of the API notes.

## Test plan

- [x] `uv run pytest tests/test_background.py`: 24/24, 100% line and branch coverage of `meals/background.py`
- [x] The five CI commands on Python 3.11 (`uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`, `pytest`): 646 passed
- [x] Flake check: 18 repeated runs of the background suite, all green (about 1.2 s each)
- [x] Hung-worker tests can't hang CI: blocked work waits on an Event capped at 5 s, polls stop after 3 s, and the deadline is 0.2 s under test
- [ ] `/steadows-ultrareview` (Codex fleet, post-PR)
- [ ] `/steadows-verify` (phase gate)
- [ ] Merge: Steve's call

## Note on the commit list

The branch was brought up to date with non-destructive `git merge origin/main`, not a reset (PR #17's branch-sync rule hasn't landed yet). So the commit list includes pre-squash originals of #9 and #15, which are already on main. The **file diff** is only the six P1 files. Squash-merging leaves main with one clean commit.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01P939VSTVLAxVqo9FkZ2zKx
