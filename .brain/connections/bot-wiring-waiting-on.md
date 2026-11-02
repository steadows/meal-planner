---
type: connection
features: [bot, wiring]
kind: waiting-on
status: watch
severity: medium
blocks: [wiring]
files: [meals/bot/, meals/prompts/intents/, tests/test_bot/]
discovered: 2026-09-25T14:17:50Z
resolved: null
updated: 2026-11-02T04:13:05Z
---
[[wiring]] builds on the bot and intent parsing from [[bot]]. wiring can start against the fakes in `meals/fakes/` once contracts has merged, but does not merge before bot. Resolves automatically when bot's presence status is `done`.
