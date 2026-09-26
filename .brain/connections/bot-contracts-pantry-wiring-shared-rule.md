---
type: connection
features: [bot, cart, contracts, pantry, wiring]
kind: shared-rule
status: watch
severity: medium
blocks: []
files: [meals/mcp_tools.py, meals/claude_runner.py, meals/bot/]
discovered: 2026-09-26T21:32:05Z
resolved: null
updated: 2026-09-26T22:15:52Z
---
**The pantry MCP tools go only into a Claude run that has no web, Chrome or file tools.** Raised by [[contracts]] (verify INFO on #24, 2026-09-26); rule and home set by [[pantry]] in PR 3 (`meals/mcp_tools.py` module docstring).

**Why:** `set_status`, `log_purchase` and `confirm_stocked` change pantry state, so a prompt-injected call could suppress Steve's pantry asks. It can't touch the product map or open URLs; PR 3 exposes no product-map writes. The MCP server can't tell who is calling, so the control is at the mount. This is Meta's "agents rule of two": never combine untrusted input (web pages, fetched recipes, meijer.com) with state-changing tools in one run. Dedupe on Telegram `update_id` still applies to the reply path; it doesn't cover this.

**The trap:** `claude_runner.ALLOWED_TOOLS` is `("WebSearch", "WebFetch")` by default (`meals/claude_runner.py:38`). So [[bot]]'s free-text run (PLAN Phase 4: "applies them through the MCP tools") must not mount the pantry tools on the default tool set.

**Who does what:**
- [[pantry]] (PR 3): documents the rule and exposes no product-map writes.
- [[contracts]] (owns `claude_runner`): if [[bot]] needs a run with `--mcp-config`, that run shape should force no web or Chrome tools, so the rule is enforced in code, not by convention.
- [[bot]] and [[wiring]]: mount the tools only on that run shape. Pass Steve's own message in; never mount the tools in the recipe-search run or the cart's Chrome run.

**From pantry PR 3's verify security review (#25, 2026-09-26):**
- **MEDIUM (LLM06):** the rule is enforced only by convention until [[contracts]] adds the `claude_runner` pantry-run shape, which asserts that no web, Chrome or file tools are attached whenever the pantry MCP config is. The review found no contradiction: `.mcp.json` mounts only chrome-devtools, and PR 3 no longer suggests `--scope project`.
- **LOW (LLM01):** tool results return item `name`, `aliases`, `notes` and `preferred_product_name` verbatim. Today only Steve's seed CSV sets them, so they're trusted. **Any future feature that fills these from scraped or external text** (for example Meijer product titles in the [[cart]] lane) **must treat that text as untrusted** before it reaches a pantry tool result.
