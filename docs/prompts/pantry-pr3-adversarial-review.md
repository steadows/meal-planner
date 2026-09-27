# Adversarial review brief — pantry PR 3: pantry MCP tools (`meals/mcp_tools.py`)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Find the issues a thorough first-pass review MISSED; do not
repeat what it already found. Findings only: do not edit any file.

## Boundaries

- Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, plus the installed `mcp`
  SDK under `.venv/lib/python3.11/site-packages/mcp/` for behaviour checks. Never read `.env`,
  anything under `data/` or `seed/`, or any real Telegram, Mealie or Meijer content.
- You do not need git: every file under review is new (see "The diff"). You may try
  `uv run pytest tests/test_mcp_tools.py -q` or a small `uv run python -c ...` probe. **If any
  command is denied or unavailable, carry on with a static review; that is not a reason to stop.**
  Cite file:line for every claim.

## Deference rule

What matters for trusting a comment, docstring or design doc is whether this diff authored it or it
was already there, not whether it's code or docs. Anything the diff adds or changes is the author's
claim, and you must verify it against the code before clearing a finding on its authority. Anything
that predates the diff is the standard the code is measured against. Where they disagree, the
pre-existing contract wins unless the diff explicitly and defensibly amends it.
- Pre-existing: `meals/contracts.py` (the `Pantry` Protocol, ~line 265), `meals/pantry.py`,
  `meals/db.py`, `meals/claude_runner.py`, `docs/PLAN.md`, `CLAUDE.md`.
- Authored by this PR: `meals/mcp_tools.py`, `tests/test_mcp_tools.py`, and the design doc it
  follows (quoted below; it is not in your readable paths).

## Not a false positive

Code that mishandles a real, reachable future condition is not a false positive just because the
trigger hasn't occurred yet. Examples: the bot mounting these tools, a second caller, a longer-lived
server. If the code as written does the wrong thing once that condition is real, it's a live finding
now.

## What this PR is

PLAN Phase 3: "Add MCP tools: `list_pantry`, `set_status`, `log_purchase`, `staples_due`,
`resolve_product`", plus `confirm_stocked`. Claude (the bot's free-text path, PLAN Phase 4) will
call these to change Steve's pantry from his chat replies. Arguments come from Claude parsing
Steve's free text, so this is an LLM-callable trust boundary.

Design (from the seam map, authored for this PR):
- `build_server(open_pantry, today=date.today) -> MCPServer`: `open_pantry()` yields a pantry for
  ONE call, and `today` is read per call.
- `open_real_pantry()` opens `get_db()` per call. The SDK runs sync tools on a worker thread, and a
  sqlite connection can't cross threads.
- Error mapping:
  - unknown name → `ToolError("no pantry item called 'x'")`;
  - pantry `ValueError` (bad qty or price) → `ToolError(str(err))`;
  - `PantryRowError` → `ToolError(str(err))`;
  - anything else propagates: the SDK logs a traceback, and the client sees only
    "Error executing tool <name>".
- `on` must be within `[today − 366 days, today + 1 day]`, checked before any pantry access.
- No tool writes the product map (`meijer_url` and so on), because the cart opens those URLs in
  Steve's logged-in Chrome.
- Descriptions are static.
- `confirm_stocked` isn't on `contracts.Pantry` yet (contracts' PR #24 adds it), hence the local
  `_PantryTools` Protocol.
- Mount rule (module docstring): a Claude run with these tools must not also have web, Chrome or
  file tools.

## The diff

Commits on `feat/pantry-mcp` over `origin/main` (5be917f):
```
4839f30 refactor: /simplify pass on the pantry MCP tools
64bb24d docs: mcp_tools mount rule (no web, Chrome or file tools in the same run)
d1fdc06 feat: pantry MCP tools (meals/mcp_tools.py)
b6abd25 test: RED for the pantry MCP tools (meals/mcp_tools.py, PR 3)
55cb2c9 chore: add mcp>=2.2,<3 for the pantry MCP tools
```
- `meals/mcp_tools.py`: NEW, read it in full.
- `tests/test_mcp_tools.py`: NEW, 64 cases, read it in full.
- `pyproject.toml`: one added dependency line, `"mcp>=2.2,<3",`.
- `uv.lock`: the added packages are attrs 26.1.0, cffi 2.1.1, cryptography 50.0.1, httpcore2
  2.13.1, httpx2 2.13.1, httpx2-jsfetch 1.0, jsonschema 4.26.0, jsonschema-specifications
  2025.9.1, mcp 2.2.0, mcp-types 2.2.0, opentelemetry-api 1.45.0, pycparser 3.0, pyjwt 2.15.0,
  python-multipart 0.0.32, pywin32 312, referencing 0.37.0, rpds-py 2026.6.3, sse-starlette
  3.4.11, starlette 1.7.0, truststore 0.10.4, uvicorn 0.54.0. A scoped pip-audit found no known
  vulnerabilities.

## What the first-pass review already caught (confidence-filtered; all will be fixed)

| # | File:line | Severity | Finding |
|---|---|---|---|
| F3 | meals/mcp_tools.py:4 | HIGH | The docstring's registration `claude mcp add ... --scope project` writes into the tracked `.mcp.json`, which already mounts chrome-devtools. That's the combination the mount rule forbids. |
| F1 | meals/mcp_tools.py:122 | MEDIUM | pydantic lax mode coerces `price_cents: true` → 1, `"499"` → 499, `qty: true` → 1.0 and `plenty: "yes"` → True before the pantry's `isinstance(bool)` check. The fix is strict types. |
| F5 | meals/mcp_tools.py:71 | MEDIUM | `except ValueError` also catches unrelated ValueErrors inside the pantry (e.g. `date.fromisoformat` on a corrupt purchase_log row), turning a bug into readable, INFO-logged text. The planned fix: constrain qty/price in the tool schema so the pantry's argument ValueErrors can't occur, then catch only `PantryRowError`. |
| F6 | meals/mcp_tools.py:113 | MEDIUM | `set_status` ("have") competes with `confirm_stocked` and `log_purchase`, and the descriptions don't say which reply maps to which tool. |
| F7 | meals/mcp_tools.py:124 | MEDIUM | The `log_purchase` description says "Repeating the same day is harmless". A same-day repeat to correct qty or price writes nothing yet returns success. |
| F9 | meals/mcp_tools.py:58 | MEDIUM | `open_real_pantry` → `get_db()` silently creates an empty DB when `PANTRY_DB` is wrong, or the server starts in another worktree. Every lookup then says "no pantry item". |
| F14 | meals/mcp_tools.py:62 | MEDIUM | `build_server` is about 77 lines with nine closures. The helpers `day` and `found` belong at module level, as the seam map planned (`_check_on`, `_unknown`). |
| F12 | tests/test_mcp_tools.py:34 | MEDIUM | The RED-phase `_MISSING` import guard and autouse fixture are dead now. |
| F2 | meals/mcp_tools.py:117 | LOW | The SDK arguments model ignores unknown keys (`extra` isn't `forbid`), so a misnamed `date`/`quantity`/`have_plenty` is dropped and the defaults are used. |
| F4 | meals/mcp_tools.py:122 | LOW | Unbounded `price_cents` (10**30) overflows SQLite, giving an opaque error. |
| F8 | meals/mcp_tools.py:99 | LOW | The `staples_due` description doesn't say to ask at most three questions (PLAN). |
| F11 | meals/mcp_tools.py:64 | LOW | `MCPServer.__init__` calls `logging.basicConfig` when the server is built (a process-wide side effect for a future mounting host). |
| F13 | meals/mcp_tools.py:75 | LOW | `day()` computes the bounds before its `on is None` return. |

Discarded as pre-existing or out of scope:
- a huge stored `typical_interval_days` overflowing pantry date maths (PR 2);
- tool descriptions not being in `meals/prompts/` (that rule covers `claude -p` task prompts).

## Your mandate

1. SKIP anything listed above. No duplicates, not even at a different severity, unless you
   believe the severity is materially wrong: then say so in one line.
2. Hunt the blind spots:
   - **Trust boundary:** what else can a prompt-injected or confused Claude do through these six
     tools? Consider argument shapes, name matching (alias/casefold collisions),
     `resolve_product` exposing `meijer_url`, and error text leaking paths or internals.
   - **Threading and lifecycle:** per-call connections on the SDK's worker thread, the connection
     closing on every path, and several concurrent tool calls against one SQLite file (WAL,
     `BEGIN IMMEDIATE`, busy timeout in `meals/db.py`).
   - **Date window:** timezone, the midnight rollover, the window at the edges of `date`, and
     `today` being read once per call.
   - **Pantry semantics:** a mismatch between what a tool does and the pre-existing Protocol
     contract (`meals/contracts.py`): replays, status transitions, `plenty` without an interval,
     the ask-date rules.
   - **Cross-file:** anything in `meals/pantry.py` or `meals/db.py` this new caller exercises
     differently from existing callers (the seed loader, the planner).
   - **Tests:** a test that passes for the wrong reason, or a fixture seeding the same wrong value
     on both sides of an assertion. Tests are high-risk tier here.
3. For each finding give:
   - **File** and **Line**;
   - **Category** (security | correctness | concurrency | compatibility | cascade-failure |
     state-machine);
   - **Severity** (CRITICAL | HIGH | MEDIUM);
   - **Finding**, **Evidence** (a probe or code path) and **Fix**.
4. If you find nothing material beyond the list, say so explicitly. Do not invent findings.

Skip pre-existing issues not introduced by this diff, pedantic nitpicks, anything a
linter/typechecker catches, and general coverage/docs remarks.
