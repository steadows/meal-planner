# Pantry PR 2: pre-PR adversarial review (single agent)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement, because the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one. Your job is to find issues that a thorough first-pass review
MISSED, not to repeat what was already found. Report findings only; do not edit code, and do not
run any command that writes to the repo.

## Data boundary (Steve, 2026-09-25): code yes, data no

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT read `.env`,
anything under `data/` (the pantry database) or `seed/` (real purchase history), or any real
Telegram, Mealie or Meijer content. There is no network access to GitHub. Work from the local
tree.

## Deference rule

When you decide whether to trust a comment, docstring or design-doc section, the dividing line
isn't code versus docs. It is authored-by-this-diff versus pre-existing.
- **Claims by this diff:** anything the diff adds or changes is the author's claim. Verify it
  against the code before clearing a finding on its authority.
- **Pre-existing text:** anything that predates the diff is the standard the code is measured
  against.
- **When they disagree:** the pre-existing contract wins, unless the diff explicitly and
  defensibly amends it.

The pre-existing contract here is:
- the `Pantry` Protocol docstrings in `meals/contracts.py` (the ask-date rule, name matching,
  `log_purchase`);
- `FakePantry` in `meals/fakes/pantry.py`, the reference `confirm_stocked` (never pulls an ask
  earlier);
- PLAN.md's Pantry rules.

The design record is `.context/seams/lane-b-pantry.md`. Revisions 5, then 4, win over the text
below them. Revision 5 is authored by this PR, so verify it.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the triggering condition hasn't
occurred yet. If the code as written produces the wrong behaviour once that condition is real,
it's a live finding now, scored on what the code does, not deferred as "future risk".

## What the change does

Pantry PR 2 builds on contracts' migration 2 (`pantry_item.next_ask_on DATE`, UNIQUE
`purchase_log(item_id, purchased_on)`), which is already on main.

- **`SqlitePantry.confirm_stocked(name, on, plenty=False)`** (`meals/pantry.py:260`) handles
  Steve's "still good" or "have plenty".
  - Status becomes `have`.
  - `next_ask_on = max(current ask date, on+7)`, or `max(current ask date, on+interval)` for
    "plenty" (`on+7` with no interval). It never pulls an ask earlier (`_postponed_ask`, :137).
  - It never changes the interval, and never logs a purchase.
  - It runs in one BEGIN IMMEDIATE transaction, and reads its result before COMMIT.
- **The ask date** (`_ask_date`, :126) is `next_ask_on` if set, else
  `last_purchased + ceil(9·interval/10)`. `staples_due` uses it.
- **`log_purchase`** (:292):
  - the latest purchase clears `next_ask_on`;
  - duplicates now use `INSERT … ON CONFLICT (item_id, purchased_on) DO NOTHING`, and a rowcount
    of 0 means a replay (:319).
- **`load_seed(items, on)`** (:355) now requires the load day.
  - **The reminder:** a seed row that ends up a staple, with an interval and no stored
    `last_purchased` or `next_ask_on`, gets `next_ask_on = on + ceil(9·interval/10)`
    (`_seed_reminder`, :167; `_insert_seed` :404; `_update_from_seed` :428). That is the
    "already in the house" bootstrap.
  - **Reporting:** `SeedResult.reminded` lists them.
  - **The CLI** (`meals/seed_loader.py:168`, :182) passes `date.today()` and prints
    "first reminder set N: …".
- **Tests:** `tests/test_pantry_confirm.py`, `tests/test_pantry_parity.py` (FakePantry ×
  SqlitePantry), plus updates in `tests/test_pantry_writes.py` and `tests/test_seed_loader.py`.

## What the first-pass review already caught (don't re-report; you may challenge a disposition)

All scored by independent checkers. The dispositions are in commits 60e52f3 (tests) and b654588
(fixes).

| # | file:line (at review time) | severity | finding | disposition |
|---|---|---|---|---|
| F1 | meals/pantry.py:139 | HIGH (80) | A replayed "plenty" after a same-day "still good" moved the ask again, because "still good" grew the interval | FIXED: growth dropped (Revision 5); interleaved-replay test added |
| F3 | meals/pantry.py:283 | MEDIUM (75) | Still-good growth never set an ask date (the next purchase re-learns the median) | FIXED: growth dropped |
| F4 | meals/pantry.py:152 | MEDIUM (75) | Growth unbounded (no MAX_INTERVAL_DAYS) | FIXED: moot |
| F6 | meals/pantry.py:445 | MEDIUM (75) | A seed re-run discarded growth | FIXED: moot |
| F8 | tests/test_pantry_confirm.py:206 | MEDIUM (75) | No interleaved replay or confirm rollback test | FIXED: both added |
| L1 | meals/pantry.py:123 | MEDIUM (75) | A code revert while migration 2 stays leaves next_ask_on rows old code ignores | ACCEPTED: revert note in the PR body |
| L4 | meals/pantry.py:390 | MEDIUM (75) | Seed reminders invisible | FIXED: `SeedResult.reminded`, CLI line, log count |
| F9 | meals/pantry.py:429 | LOW (75) | `columns` dict mutated | FIXED |
| L2 | meals/pantry.py:288 | LOW (72) | Growth not logged | FIXED: moot |
| F2 | meals/pantry.py:142 | LOW (60) | When the 90% point beats the push, "still good" freezes that date into next_ask_on; a later interval correction can't move it until the next purchase. The fake does the same | ACCEPTED as a known limitation; joint fake+real follow-up with contracts (leave NULL when the current ask date already wins) |
| F7 | meals/pantry.py:451 | LOW (50) | `has_ask_date` misnamed | FIXED: renamed `has_purchase_or_reminder` |
| F10 | meals/pantry.py:307 | LOW (50) | Line over 100 characters | FIXED |
| L3 | meals/pantry.py:350 | LOW (50) | Restock clearing next_ask_on not logged | FIXED |
| F5 | meals/pantry.py:279 | discarded (0) | One invalid row blocks confirm_stocked (get_item validates all rows) | Pre-existing PR 1 design: fail closed, loudly (PantryRowError) |

Other decisions, already made (challenge them only with evidence):
- **The bootstrap day** is the 90% point from the load day. Half the interval would nag weekly
  about a full bottle.
- **An old replay after an intervening "out of X" flip** re-sets `have`. That's accepted;
  exactly-once belongs to the bot's Telegram `update_id` dedupe.

## The diff to review

`git diff origin/main...HEAD` on branch `feat/pantry`, five commits b6b8646..b654588. Read in
full:
- `meals/pantry.py`
- `meals/seed_loader.py`
- `meals/fakes/pantry.py` (the reference; pre-existing)
- `meals/contracts.py` (the `Pantry` Protocol and `PantryItem`; pre-existing)
- `meals/db.py` (MIGRATIONS; pre-existing)
- the four test files above

## Your mandate

1. SKIP anything already covered above. No duplicates.
2. Focus on the blind spots automated reviewers commonly miss:
   - **Subtle logic errors under specific input combinations:** ask-date arithmetic at the
     boundaries; `on` before `last_purchased`; datetimes; an item whose category or interval
     changes between calls.
   - **Cross-file interactions:** does anything in `meals/` (search's planner, the fakes, other
     callers of `load_seed` or `SeedResult`) break on the new required `on` or the new field?
   - **Concurrency:** two processes (the bot, a job, an MCP server) calling
     `confirm_stocked`/`log_purchase`/`load_seed` at once under WAL plus BEGIN IMMEDIATE.
   - **Error handling gaps where failures cascade silently:** `ON CONFLICT` rowcount semantics;
     `date + timedelta` overflow near `date.max`.
   - **State machine violations:** status and `next_ask_on` transitions across confirm, flip,
     purchase and seed in any order.
   - **Parity:** do FakePantry and SqlitePantry really agree everywhere the parity test claims,
     or does the script dodge a real divergence?
3. For each finding, give:
   - **File** and **Line**.
   - **Category:** correctness | concurrency | compatibility | cascade-failure | state-machine |
     security.
   - **Severity:** CRITICAL | HIGH | MEDIUM.
   - **Finding:** what the first pass missed and why it matters.
   - **Evidence:** a concrete input sequence and the wrong output. Reproduce it if you can, with
     a read-only `uv run python -c` against an in-memory `meals.db` connection.
   - **Fix:** a concrete recommendation.
4. If the first pass was thorough and you can't find material issues, say so explicitly. Don't
   invent findings to justify the review.
