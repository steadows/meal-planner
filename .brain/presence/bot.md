---
type: presence
agent: bot
feature: "Telegram bot: handlers, free-text intents, voice notes, commands"
status: idle
phase: waiting on contracts + existing-bot answers
owns_branches: ["bot"]
plan: docs/PLAN.md (Implementation plan, Concurrency lanes)
tracker_epic: none
current_worktree: /Users/stevemeadows/meal-planner-bot
current_branch: feat/bot
current_ticket: none
touches: [meals/bot/, meals/prompts/intents/, tests/test_bot/]
updated: 2026-09-25T14:17:50Z
---
Lane D. Separate bot token, shared codebase pattern with the agentic-OS bot (PLAN.md → Reuse the existing bot). Free text and voice → Intent list via claude_runner → pantry ops; always echo back what was understood; lock to Steve's chat ID. Done when 'out of eggs' by text and by voice updates the pantry (milestone M1 with [[pantry]]). Waits on [[bot-contracts-waiting-on]] and on Steve's answers in [[bot-waiting-on]]. Unblocks [[bot-wiring-waiting-on]].

## Telegram setup done (Steve, via [[pm]], 2026-09-25)
New bot **@steve_meals_bot** ("Meal Planner") created with BotFather. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_ID` (Steve's private chat) are in `~/meal-planner/.env`, symlinked into this worktree as `.env` (git-ignored). getMe and a test sendMessage both succeeded. Only one process may poll this token at a time: if the real bot is ever running on the home machine, integration tests here will get a 409 conflict, so ask Steve for a separate test bot then. The existing-bot decision is "start fresh" (see [[bot-waiting-on]], resolved).

## Start gate (Steve, via [[pm]], 2026-09-25)
Besides contracts, the bot lane waits on the runtime-model design that [[wiring]] runs through `/steadows-architect`. See [[bot-runtime-design-waiting-on]].

## Open contract gaps you own (via [[pm]], 2026-09-26)
- Typed per-kind `Intent` args (a discriminated union): propose them as a contracts PR when you build `intents.py`. See [[bot-contracts-intent-typed-args]].
- "Still good" vs "have plenty": you need `Pantry.confirm_stocked` to tell pantry which one Steve meant. See [[bot-contracts-pantry-confirm-stocked]].

## Security note from Lane 0 /steadows-verify (via [[pm]], 2026-09-26)
`ClaudeRunnerError.raw_output` can contain fetched web-page text. It's untrusted: when surfacing an error to Steve in Telegram, send it as plain text (no `parse_mode`), truncated, and never render it as markdown or HTML.
