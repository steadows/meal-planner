# Post-PR ultrareview brief — pantry PR 3 (GitHub PR #25, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
The branch is `feat/pantry-mcp` in this working tree. The PR head is 6248c8a, not counting this
brief's own commit. The PR description is reproduced verbatim below. Don't ask for it; everything
you need is local.

- **The diff, without git:** git may be unavailable in your sandbox.
  - `meals/mcp_tools.py` and `tests/test_mcp_tools.py` are NEW files. Read them in full.
  - `pyproject.toml` gains one line, `"mcp>=2.2,<3",`.
  - `uv.lock` gains the mcp 2.2.0 tree: attrs, cffi, cryptography, httpcore2, httpx2,
    httpx2-jsfetch, jsonschema(-specifications), mcp, mcp-types, opentelemetry-api, pycparser,
    pyjwt, python-multipart, pywin32, referencing, rpds-py, sse-starlette, starlette, truststore
    and uvicorn.
  - `docs/PLAN.md` ticks "Add MCP tools…" (#25).
  - `docs/prompts/pantry-pr3-*.md` are review briefs.
  - A merge of origin/main brought in unrelated, already-reviewed main changes.
  - If `git` works, `git diff origin/main...HEAD` shows the same.
- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill. Treat these as higher-risk and give them full weight:
  - **Security, the trust boundary:** these tools are called by Claude parsing Steve's free text.
    Consider what a confused or prompt-injected caller can do: argument validation and coercion,
    name/alias matching, error text disclosure, and product-map exposure via
    `resolve_product`. The mount rule lives in the module docstring.
  - **Concurrency:** one sqlite connection per tool call, on the SDK's worker thread, and several
    processes against one WAL database (`meals/db.py`, `BEGIN IMMEDIATE` writes in
    `meals/pantry.py`).
  - **State machine and replay safety:** writes are delivered at least once. `log_purchase` and
    `confirm_stocked` REQUIRE the message's date, so a retry repeats it. Check the pantry's
    replay semantics through these tools.
  - **Supply chain:** the new `mcp` dependency tree.
- **REVIEW ONLY:** do not edit, commit or push anything.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `uv.lock`, `.github/`, `.context/seams/`, and the installed SDK under
  `.venv/lib/python3.11/site-packages/mcp/`. Never read `.env`, `data/` or `seed/`, or any
  real Telegram, Mealie or Meijer content.
- **Prior context:**
  - `.context/seams/lane-b-pantry-pr3-mcp-tools.md`: Revisions 2 and 1 at the top win. Both
    are this PR's own amendments, so verify them.
  - The pre-existing contract: the `Pantry` Protocol docstrings in `meals/contracts.py`,
    `meals/pantry.py`, `meals/db.py`, `meals/claude_runner.py`, and PLAN.md's Pantry rules.
  - The pre-PR review's findings, dispositions and fixes: `docs/prompts/pantry-pr3-adversarial-review.md`
    (its table lists every first-pass finding) and the commit messages. Its Codex sweep then
    added one MEDIUM (a purchase retried after midnight was re-dated by a clock default), fixed
    in 12f9c86 by requiring `on`.
  - Don't re-raise a finding already fixed or deliberately accepted there (the "Known
    limitations" below), unless the fix or the acceptance is wrong.
- **Probes:** run them where cheap and read-only. `uv run pytest tests/test_mcp_tools.py -q`, or a
  `uv run python -c` driving `mcp.Client(build_server(...))` over `meals.fakes.FakePantry` or a
  temp-file `meals.db.get_db(Path)`. **If a command is denied, continue statically; it is not a
  reason to stop.**
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), evidence and a concrete
  fix. Say explicitly if nothing material is found.

---

## PR #25 description (verbatim)

**Phase 3 / Lane B: pantry PR 3.** This gives Claude six tools to read and update Steve's pantry, so the bot's free-text path (PLAN Phase 4) can turn "bought rice", "still have butter" or "out of olive oil" into pantry changes.

## What ships
`meals/mcp_tools.py` (new) is an MCP server built on the official `mcp` 2.2 SDK (`MCPServer`, stdio: `uv run python -m meals.mcp_tools`).

| tool | does |
|---|---|
| `list_pantry` | every item |
| `staples_due(on?)` | staples to ask about (default today); callers ask about three at most |
| `resolve_product(name)` | item by name or alias, with its Meijer product |
| `set_status(name, status)` | `buy_next_time` when out; `have` only undoes that |
| `log_purchase(name, on, qty?, price_cents?)` | a purchase, dated by Steve's message |
| `confirm_stocked(name, on, plenty?)` | "still good" / "we have plenty" |

- **One pantry per call:** `build_server(open_pantry, today)` takes an opener. `open_real_pantry()` opens sqlite inside each call, because the SDK runs sync tools on a worker thread. It refuses a missing database instead of creating an empty one.
- **Trust boundary (arguments come from Claude parsing free text):**
  - strict types (`qty` > 0 and finite, `price_cents` ≥ 0, `plenty` a real bool);
  - `on` must fall in `[today − 366, today + 1]`;
  - both are checked before the pantry opens;
  - an unknown name or a corrupt row becomes a `ToolError` Claude can act on, and anything else stays opaque, with its traceback logged on stderr;
  - no tool can write the product map (`meijer_url` and so on).
- **Writes take the date of Steve's message.** `log_purchase` and `confirm_stocked` require `on`, and there's no clock default. The Codex sweep showed a clock default re-dates a retried purchase after midnight, which slips past the pantry's one-purchase-per-day replay check.
- **Stand-in interface:** a local `_PantryTools` Protocol adds `confirm_stocked` until #24 puts it on `contracts.Pantry`. It gets deleted after that.
- New dependency `mcp>=2.2,<3` (one add-only line in `pyproject.toml`; the rest is `uv.lock`). pip-audit, scoped via `uv export`, found no known vulnerabilities.
- PLAN: ticks "Add MCP tools…" (all five named tools ship, plus `confirm_stocked`).

## For other lanes
- **@bot:** pass `on` = the date of Steve's Telegram message on `log_purchase` / `confirm_stocked`, so a retried run repeats the same date. Map the replies: "bought" → `log_purchase`; "still good" / "plenty" → `confirm_stocked`; "ran out" → `set_status buy_next_time`.
- **Mount rule** (`.brain/connections/bot-contracts-pantry-wiring-shared-rule.md`): a Claude run with these tools must have **no web, Chrome or file tools**. `claude_runner`'s defaults are WebSearch/WebFetch, and the project `.mcp.json` mounts chrome-devtools, so mount the tools only through the bot's own run config. @contracts will add a `claude_runner` run shape that enforces this when bot asks.

## Known limitations (accepted, LOW)
- The SDK ignores unknown argument names, so a misnamed *optional* argument falls back to its default. A misnamed required `on` is still refused.
- A `price_cents` above 2^63 overflows SQLite and returns an opaque error. No bad data is written.
- `MCPServer()` calls `logging.basicConfig` when the server is built.

## Gates run
- Seams: `.context/seams/lane-b-pantry-pr3-mcp-tools.md` (tier high; Revisions 1–2).
- RED via test-writer, then one spec-watchdog pass. Its reference build killed 22 of 30 mutants; 5 fixes followed.
- Mutation checks on this implementation: 12/12, then 8/8 after the repairs.
- `/simplify`: 1 test fix and 1 docstring fix.
- `/steadows-code-review`: 15 first-pass findings, confidence-scored. 8 confirmed and fixed, 2 of the 5 LOW fixed, 2 discarded. The Codex single-agent sweep found 1 new MEDIUM (the midnight retry), fixed. Only 1 finding was test infrastructure.
- CI five locally: `uv sync --locked`, ruff check, ruff format --check, mypy, pytest (812 passed), plus lint-imports.

## Test plan
- [x] `uv run pytest tests/test_mcp_tools.py` passes all 77 cases, including a real-DB write on the SDK worker thread and a `python -m meals.mcp_tools` stdio smoke test.
- [x] Full suite: 812 passed.
- [ ] CI green on this PR.
- [ ] `/steadows-ultrareview` (Codex fleet) on this PR.
- [ ] `/steadows-verify` end of phase.

