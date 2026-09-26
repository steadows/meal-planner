# Adversarial review — pantry lane PR 1 (pre-PR gate, single agent)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Your job is to find issues that a thorough first-pass review
MISSED, not to repeat what was already found. Findings only: do not edit code.

**Data boundary (docs/AUTONOMOUS_WORK.md §3):** you may read `meals/`, `tests/`, `docs/`,
`pyproject.toml`, `.github/` and `.context/seams/`. Do NOT read `.env`, `data/` or `seed/`.

## Deference rule
The dividing line for trusting a comment, docstring or design-doc section is not code vs docs. It
is authored-by-this-diff vs pre-existing. Anything the diff under review adds or changes is a claim
by the author, and must be verified against the code before you clear a finding on its authority.
Anything that predates the diff is the standard the code is measured against: `docs/PLAN.md`,
`CLAUDE.md`, `docs/AUTONOMOUS_WORK.md`, `meals/contracts.py`, `meals/db.py`. The seam map
`.context/seams/lane-b-pantry.md` was written by this lane; its Revisions 1–3 record decisions
made with the contracts lane and the pm lane, but verify the code matches them.

## Not a false positive
Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the trigger hasn't happened yet. If
the code as written produces the wrong behaviour once that condition is real, it's a live finding
now.

## What the first-pass review already caught (fix in progress, do not repeat)
- MEDIUM `meals/seed_loader.py:92`: a CSV row with more cells than the header crashes with
  AttributeError (the extra cells land under key None), instead of being reported as a bad row.
- HIGH `meals/pantry.py:148`: one invalid pantry_item row (e.g. a hand-edited meijer_url) makes
  `list_items()` raise, which disables every operation, including `load_seed`, the only repair
  path.
- MEDIUM `meals/pantry.py:285`: `load_seed` treats "≥2 purchase dates" as "learning owns the
  interval" without the staple check, so a perishable or fallback item's interval freezes.
- LOW `meals/seed_loader.py:128`: row numbers count records, not file lines (blank lines,
  multi-line cells).
- MEDIUM `meals/pantry.py:35`: SeedItem allows huge `interval_days` (a date overflow in
  `staples_due`) and infinite `default_qty`.
- MEDIUM `meals/seed_loader.py:157`: `main()` doesn't catch `sqlite3.Error` (a bad `--db` file, a
  lock timeout) and shows a raw traceback.
- MEDIUM `meals/rollup.py:172`: `combine` rounds to 6 decimals, but `packages_needed` has only
  1e-9 slack, so it can overbuy a pack (1 tbsp + 2 tsp → 6 tsp packs).
- MEDIUM `meals/pantry.py:203`: a `datetime` passed as `on` crashes `log_purchase` and
  `staples_due`.
- LOW `meals/pantry.py:325`: `_write()` commits outside the try, so a failed COMMIT isn't rolled
  back.
- MEDIUM `meals/pantry.py:197`: `price_cents` isn't checked to be an int, so a float in dollars is
  stored 100x wrong.
- LOW `meals/rollup.py:84`: `_key` duplicates `pantry._match_key`.
- MEDIUM `meals/pantry.py:28`: SeedItem re-declares PantryItem's fields by hand, so they can drift.
- LOW `meals/pantry.py:283`: the median is computed just to count dates; an unused `conn`
  parameter.
- MEDIUM `meals/pantry.py` (whole file): no logging on mutations (db.py uses `logging`).

## The diff to review
`git diff main...HEAD` on branch `feat/pantry`. Read in full: `meals/pantry.py`,
`meals/rollup.py`, `meals/seed_loader.py`, and the tests `tests/test_pantry.py`,
`tests/test_pantry_writes.py`, `tests/test_rollup.py`, `tests/test_seed_loader.py`, plus the
pantry block at the end of `tests/conftest.py`. Also relevant: `docs/PLAN.md` ("Pantry rules",
"Learning when staples run out", "Implementation plan", "Runtime concurrency"), `meals/contracts.py`
(the `Pantry` Protocol, `PantryItem`, `MeijerUrl`) and `meals/db.py`.

Context:
- One user, one home Mac, about 50 pantry rows.
- Several local processes may write to the same SQLite file in WAL mode: a Telegram bot, launchd
  jobs, and an MCP server.
- The seed CSV is hand-exported by Steve and loaded by a CLI.
- `meijer_url` values are later opened by a logged-in Chrome session, so the meijer.com-only
  boundary matters.
- **A pending contracts-lane PR** (not in this diff) adds `PantryItem.next_ask_on` plus a
  migration. This PR must stay green whether it merges before or after that one.

## Your mandate
1. SKIP anything already covered above: no duplicates.
2. Focus on blind spots:
   - subtle logic errors under specific input combinations;
   - cross-file interactions (the contract Protocol vs the real class, fakes vs the real class,
     the conftest insert helper vs the schema);
   - trust boundaries (CSV → DB → cart URL);
   - concurrency (BEGIN IMMEDIATE usage, reads vs writes across processes, replays);
   - error handling that cascades silently;
   - compatibility with the pending contracts change;
   - the learned-interval state machine.
3. For each finding give:
   - **File**
   - **Line**
   - **Category**: security | correctness | concurrency | compatibility | cascade-failure | state-machine
   - **Severity**: CRITICAL | HIGH | MEDIUM
   - **Finding**
   - **Evidence**: why it's real, not speculative; run a probe if cheap
   - **Fix**
4. If the first pass was thorough and you find nothing material, say so explicitly. Don't invent
   findings.
