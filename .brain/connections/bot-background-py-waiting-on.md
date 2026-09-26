---
type: connection
features: [bot]
kind: waiting-on
status: resolved
severity: medium
blocks: [bot]
files: [meals/bot/]
discovered: 2026-09-26T14:23:03Z
resolved: 2026-09-26T20:42:20Z
updated: 2026-09-26T20:42:20Z
---
[[bot]] builds against [[wiring]]'s `meals/background.py` (`background.start` / `spawn_job`), which wiring ships first as ADR-0001 phase **P1**. External-only on purpose: wiring's lane status reaches `done` only when it merges last, but the blocker here is the P1 PR. Resolve by hand (status: resolved) once the P1 PR is on main. Replaces the now-resolved [[bot-runtime-design-waiting-on]]. [[pm]] tracks it.

## API notes for [[bot]] (from [[wiring]], P1 PR, 2026-09-26)
- `await background.start(update, context, work, render, ack)` from the handler. It returns at once. `work` is a sync callable run in a worker thread, and **it must have a bounded runtime** (claude_runner, httpx and SQLite all do). `render(result) -> str` runs on the event loop, so keep it to formatting.
- **Every reply is plain text** (`parse_mode=None` explicitly, which overrides any `Defaults(parse_mode=…)`). `render` output included: recipe titles and error text are untrusted web or Claude text. Replies over 4096 chars are split into several messages.
- At most 2 in flight, and a 3rd gets "busy" (its work never runs). After 15 min the user is told it gave up. The slot stays held until the thread really ends.
- Errors come back as one line, "Sorry, that didn't work: <first line>", and never include `ClaudeRunnerError.raw_output`.
- `background.spawn_job(name, *args) -> pid` starts `python -m meals job …` detached. **It raises OSError**, so wrap it and compose your own message ("approved, but I couldn't start the cart fill; I'll retry within the hour").
- **Two Application-level settings for you to decide** (Codex pre-PR sweep, left out of background.py on purpose):
  1. PTB's `AIORateLimiter` (the `python-telegram-bot[rate-limiter]` extra) paces all sends, but retries a `RetryAfter` **only with a positive `max_retries`**. The default is 0 (`telegram/ext/_aioratelimiter.py:146`), so use e.g. `AIORateLimiter(max_retries=3)`. Without it, a multi-chunk reply can hit flood control partway through (ultra fleet LOW, PR #20).
  2. `concurrent_updates`: by default PTB handles updates one at a time, so any in-handler send (including background's "busy" reply) holds up the next update while Telegram is slow.
- **Security notes (P1 /steadows-verify, 2026-09-26):** `start` and `spawn_job` do no authorization of their own. They rely on your chat-ID allowlist in the pre-dispatch `TypeHandler` (ADR-0001 Ownership), so never call them from outside that dispatch chain. The `spawn_job` child inherits the bot's full environment (it needs `TELEGRAM_BOT_TOKEN` to send its report), which is fine for first-party jobs. Never pass chat text as `spawn_job`'s name or args: only literal job names and values from the DB (e.g. the week), so nothing starting with `-` can be misread by the job's argparse.


**Resolved 2026-09-26T20:42:20Z by [[wiring]]:** P1 is on main. PR #20 was merged by Steve's go as merge commit 28885e1. [[bot]] can build against `meals.background.start` / `spawn_job` now; the API notes above still apply.
