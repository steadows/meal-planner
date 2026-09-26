---
type: connection
features: [bot, contracts]
kind: shared-file
status: watch
severity: medium
blocks: []
files: [meals/contracts.py, meals/bot/intents.py]
discovered: 2026-09-26T03:24:11Z
resolved: null
updated: 2026-09-26T03:24:11Z
---
**Open contract gap (from the [[contracts]] Codex sweep, routed by [[pm]]).** `Intent.args` is a plain `dict[str, str]` with no shape per `kind`, so `{'item': 'rice', 'status': 'delete'}` validates as a `pantry_flip`. When [[bot]] builds `intents.py`, it should propose typed arguments per kind: a pydantic discriminated union on `kind` covering pick, swap, custody, pantry_flip, add_item, find, save, rate and mode. Send it as a small PR to [[contracts]]. The bot lane knows each intent's real arguments; contracts deliberately didn't invent them. Set status resolved once it's on main.
