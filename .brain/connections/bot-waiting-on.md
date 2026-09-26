---
type: connection
features: [bot]
kind: waiting-on
status: resolved
severity: medium
blocks: [bot]
files: [meals/bot/]
discovered: 2026-09-25T14:17:50Z
resolved: 2026-09-26T01:24:22Z
updated: 2026-09-26T01:24:22Z
---
External blocker (Steve): five answers about the existing agentic-OS Telegram bot decide how [[bot]] is built — (1) language and Telegram library, (2) polling or webhook, (3) does it run on the home machine, (4) does it call Claude and how (API key or Claude Code), (5) does it handle voice notes. Default if unanswered: new bot token, python-telegram-bot, polling, same codebase patterns. Whoever gets the answers records them here and sets status: resolved.

**Resolved 2026-09-25 (Steve, via [[pm]]): start fresh — no reuse.** The agentic-OS bot isn't on this machine, and the Telegram bot that is here (`~/dinnerbot`) runs on Google Cloud via webhook and calls Gemini, which the plan's reuse table maps to "fully separate new bot". So the default holds: (1) Python + python-telegram-bot, (2) polling, (3) runs on the home machine, (4) Claude via `claude -p` through `meals/claude_runner.py`, (5) the new bot handles voice itself per PLAN Phase 4 (transcription method is the bot lane's call; PLAN lists faster-whisper + ffmpeg). New token from BotFather, locked to Steve's chat ID. DinnerBot's handler code is fair game to borrow patterns from.
