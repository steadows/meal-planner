# Post-PR ultrareview brief — pantry PR 1 (GitHub PR #12, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
Review `git diff main...HEAD` on branch `feat/pantry` in this working tree (HEAD = the PR head,
1b2bc11). The PR description is reproduced verbatim below.

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill. Treat these as higher-risk:
  - concurrency: SQLite `BEGIN IMMEDIATE` read-modify-write shared by several local processes;
  - the trust boundary: seed CSV → DB → a `meijer_url` later opened by a logged-in Chrome session,
    which may only open meijer.com.
- **REVIEW ONLY:** do not edit, commit or push anything.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `.github/` and `.context/seams/`. Never read `.env`, `data/` or `seed/`.
- **Prior context:**
  - `.context/seams/lane-b-pantry.md` (Revisions 1–3 at the top win).
  - The pre-PR review's findings and fixes, in the commit messages
    (`git log --format='%h %s%n%b' main..HEAD`).
  - `docs/prompts/pantry-pr1-adversarial-review.md`.
  - Don't re-raise a finding those already fixed, unless the fix is wrong.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), evidence (run probes
  where cheap) and a concrete fix. Say explicitly if nothing material is found.

---

## PR #12 description (verbatim)

## What this adds

The pantry lane's first PR (Lane B, PLAN.md Phase 3). The pantry now knows what's in the house and what to buy. It works out which staples are due, learns how fast each staple runs out, logs purchases, and loads the seed-run product map from a CSV. It also adds the quantity maths the cart uses: ½ + ½ onion = 1, 1 lb + 8 oz = 1.5 lb, 2 cloves + 1 head of garlic = 12 cloves. That maths rounds up to whole packs.

**Unblocks the cart and wiring lanes.** This PR is green on main whether it merges before or after the contracts PR that widens the `Pantry` Protocol. See "Merge order" below.

## What's in it

- **`meals/rollup.py`** (pure maths)
  - `combine(ingredients)` sums lines by name. Within a name, lines that convert sum into the first-seen unit; units that can't convert stay as separate lines. The unit table covers mass, US volume, dozen, and garlic head ↔ clove.
  - `packages_needed(need, pack_qty, pack_unit)` rounds up to whole packs.
  - The unit table is kept by hand. pint was rejected: it has no count units and it dropped Python 3.11. Mealie, Grocy and Tandoor all use a small per-ingredient table (`.brain/research/ingredient-unit-rollup.md`).
- **`meals/pantry.py`**: `SqlitePantry` implements `contracts.Pantry`.
  - `get_item` and `list_items`: name or alias match, casefolded; a name beats another item's alias.
  - `staples_due`: flagged items, or those on or after the ask date (90% of the interval since the last purchase, computed with exact integers). Sorted flagged first, then days past the ask date, then name.
  - `flip_status`.
  - `log_purchase`:
    - The latest purchase restocks the item; a back-dated one only adds history.
    - A staple with 2 or more distinct purchase dates learns the median gap, rounded half up.
    - A replay of the same (item, day) writes nothing.
  - `load_seed`:
    - All or nothing. It matches items by name only.
    - It inserts new items, with a `seed` purchase row only for a real purchase date.
    - It rewrites an existing item's product map, but never its status, last purchase or history.
    - The interval follows the seed until learning owns it.
    - It rejects a seed that would give two items the same name or alias.
  - Every read-modify-write runs in one `BEGIN IMMEDIATE` transaction, and the return value is read before commit. The write helper refuses a connection that already has a transaction open.
- **`meals/seed_loader.py`**: `uv run python -m meals.seed_loader <csv> [--db PATH]`.
  - Validates every row: UTF-8 with or without a BOM, strict CSV, strict booleans, ISO dates, bounds on numbers.
  - Raw non-ASCII in a URL is percent-encoded, and the URL is then held to the meijer.com-only rule.
  - Reports every bad row by file line, and loads nothing until the file is clean.
- **Tests:** 329 pass. Coverage: pantry 98%, rollup 100%, seed_loader 98%.

## For Steve: the seed CSV format

The Phase 0 export should use this header. Only `name` and `category` are required.

```
name,category,aliases,interval_days,last_purchased,default_qty,default_unit,meijer_product_id,meijer_url,preferred_product_name,substitute_ok,for_miles,notes
```

- `aliases` are `;`-separated.
- `last_purchased` must be a *real* purchase date. Leave it blank for things already in the house.
- `substitute_ok` and `for_miles` take 1/0, yes/no or true/false.
- There's an example in `tests/fixtures/seed_pantry.csv`.

## Decisions worth a look

- **A hand-corrupted row fails closed, loudly.** Say a stored `meijer_url` isn't meijer.com. Every read then raises `PantryRowError`, which says "pantry_item 7 ('rice') is invalid; re-run the seed loader…". `load_seed` doesn't need the bad row to validate, so re-running the seed repairs it.
  - *Alternative rejected:* quarantine the row and keep going. That silently shrinks the pantry, so the planner stops asking about the item and the cart misses it.
  - **Cross-lane:** this text must reach Steve through wiring's job-failure Telegram message, not only the log. pm has flagged this to wiring.
- **"Out of X shortens the estimate"** happens through the short gap it records. The median resists a one-off outlier. If faster adaptation is wanted, the option is a median over the last 3 gaps.
- **Staples-due order** is by days past the ask date, not by overdue ratio. It's agreed with contracts, whose PR moves `FakePantry` to the same rule. Until that PR merges, the fake on main still sorts by ratio. The two agree on the shared fixture but can differ on other data.

## Merge order and follow-ups

- **Contracts PR (in flight).** It adds a `next_ask_on` migration, a UNIQUE `(item_id, purchased_on)` index, and `log_purchase`, `get_item` and `list_items` on the Protocol.
  - This PR implements all of those methods.
  - It maps every row column, so `next_ask_on` flows through.
  - It has no test setup that writes duplicate purchase rows.
  - So it's green in either order.
- **Pantry PR 2** (after the contracts migration): `confirm_stocked` ("still good" / "have plenty") on `next_ask_on`, a bootstrap reminder for staples already in the house, and a FakePantry × SqlitePantry parity test. Then a one-line contracts PR adds `confirm_stocked` to the Protocol.
- **Pantry PR 3:** `meals/mcp_tools.py`. It needs the new `mcp` dependency and has no code consumer yet.

## Gates run

Seams (with a Codex thought-partner critique of the design) → TDD, split by tier:
- Ordinary tier: rollup and pantry reads, where the lane wrote RED itself.
- High tier: purchase and seed writes and the seed loader, with RED from test-writer and one spec-watchdog pass, frozen before GREEN.

Then the local CI five on 3.11 → `/simplify` → `/steadows-code-review`:
- `/code-review` plus a lens reviewer: 16 findings, 11 kept after confidence scoring.
- A single-agent Codex adversarial sweep: 5 MEDIUM, 4 accepted.
- Every CRITICAL and HIGH finding is fixed.

## Test plan

- [x] `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy` and `pytest` pass under Python 3.11 locally
- [x] Live CLI run:
  - fixture load: 8 inserted;
  - re-run: 8 updated;
  - unknown column: exit 1;
  - missing file: exit 1;
  - `staples_due` on the seed: olive oil, tahini, butter.
- [ ] CI green on this PR
- [ ] Post-PR `/steadows-ultrareview` (Codex)
- [ ] Steve: confirm the seed CSV header before the Phase 0 export

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_019DV3RhXumk2PyGTHgTPeX3
