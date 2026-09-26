# Post-PR ultrareview brief — pantry PR 2 (GitHub PR #19, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
Review `git diff origin/main...HEAD` on branch `feat/pantry` in this working tree. HEAD is the PR
head, 1287120, not counting this brief's own commit. The PR description is reproduced verbatim
below. Don't ask for it; everything you need is local.

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill. Treat these as higher-risk and give them full weight:
  - **Concurrency:** SQLite (WAL) `BEGIN IMMEDIATE` read-modify-write, shared by several local
    processes (the bot, scheduled jobs, an MCP server). Look at `confirm_stocked`,
    `log_purchase` and `load_seed`.
  - **The state machine:** status, `next_ask_on` and `last_purchased` across "still good" /
    "plenty" / "out" flips, purchases (latest, back-dated, replayed) and seed re-runs, in any order.
  - **Replay safety:** replies and purchases are delivered at least once.
- **REVIEW ONLY:** do not edit, commit or push anything.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `.github/` and `.context/seams/`. Never read `.env`, `data/` or `seed/`, or
  any real Telegram, Mealie or Meijer content.
- **Prior context:**
  - `.context/seams/lane-b-pantry.md`: Revision 5, then 4, at the top win. Revision 5 is this
    PR's own amendment, so verify it.
  - The pre-existing contract: the `Pantry` Protocol docstrings in `meals/contracts.py`,
    `FakePantry` in `meals/fakes/pantry.py`, and PLAN.md's Pantry rules.
  - The pre-PR review's findings, dispositions and fixes: the commit messages
    (`git log --format='%h %s%n%b' origin/main..HEAD`) and
    `docs/prompts/pantry-pr2-adversarial-review.md`, whose table lists every first-pass finding.
  - Don't re-raise a finding already fixed or deliberately accepted there, unless the fix or the
    acceptance is wrong.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), evidence (run probes
  where cheap: a read-only `uv run python -c` against a temp-file `meals.db.get_db(Path)`
  connection) and a concrete fix. Say explicitly if nothing material is found.

---

## PR #19 description (verbatim)

## What this does

Steve can now answer a pantry question with **"still good"** or **"we have plenty"**, and the pantry pushes the next question back instead of flipping the item to `have` and forgetting. Staples that were already in the house when the seed CSV is loaded get a **first reminder**, so they're eventually asked about instead of never.

Pantry PR 2 (Lane B, task B3). It builds on contracts #13 (migration 2: `pantry_item.next_ask_on`, UNIQUE `purchase_log(item_id, purchased_on)`), which is already on main. It is independent of every open PR.

## Changes

**`SqlitePantry.confirm_stocked(name, on, plenty=False) -> PantryItem | None`** (`meals/pantry.py`)
- **Status and the log:** status becomes `have`. No purchase is logged.
- **The next ask:** "still good" asks again at `on + 7`, and "plenty" at `on + interval` (`on + 7` with no interval). It takes the later of that and the current ask date, so it **never pulls an ask earlier**. That matches the merged `FakePantry`.
- **Replays:** it never changes the interval, and repeating any mix of these replies for the same `on` changes nothing. That makes at-least-once replays safe.
- **Transactions:** one `BEGIN IMMEDIATE` transaction, with the result read before COMMIT, like the other writes.

**The ask date** is `next_ask_on` when set, else `last_purchased + ceil(9·interval/10)` (the Protocol's rule). `staples_due` uses it, and a `buy_next_time` flag still wins.

**`log_purchase`**:
- The latest purchase clears `next_ask_on`; a back-dated purchase or a replay leaves it.
- Duplicate detection stays main's read-then-insert, with the new UNIQUE index as a backstop. Relying on INSERT's rowcount was tried and reverted: `PRAGMA count_changes` zeroes it.

**`load_seed(items, on)`** now takes the load day. `seed_loader`'s CLI passes today.
- **The reminder:** a seed row that ends up a staple, with an interval and no purchase or ask date ("already in the house"), gets `next_ask_on` at 90% of its interval from the load day. No purchase date is invented, and a re-run never moves the reminder.
- **Reporting:** `SeedResult.reminded` lists them, and the CLI prints `first reminder set N: …`.

**Tests:**
- `tests/test_pantry_confirm.py` (new);
- `tests/test_pantry_parity.py` (new): FakePantry × SqlitePantry run the same script and must agree on every returned item, whole items including the interval, and on `staples_due` at real due lines;
- `load_seed(…, on)` call sites and new cases in `tests/test_pantry_writes.py` and `tests/test_seed_loader.py`.

## Decisions for Steve (revisitable, each one sentence to change)

1. **"Still good" no longer lengthens the estimate directly.** PLAN says it "lengthens the estimate a little". Code review showed that growing the interval on "still good" never moved an ask date, because the next purchase re-learns the median, and it made a replayed "plenty" move the ask again. So the estimate now lengthens through the long gap the next purchase records, the same way "out of X" shortens it through the short gap (the deviation already accepted in PR #12).
2. **The first ask for an already-owned staple** is the 90% point counted from the day the CSV is loaded, as if it were bought that day. Asking at half the interval would nag every week about a full bottle ("still good" only adds a week). An early run-out is covered by "out of X" at any time.

## Known limitations (accepted)

- **An unprompted "still good" more than a week before an item is due** stores the current 90% date in `next_ask_on`. Until the next purchase, two things can't move it: a corrected seed interval, and a back-dated purchase that re-learns a shorter interval. The fake does the same. The fix ("leave `next_ask_on` empty when the current ask date already wins") has to land in the fake and the real pantry together, or parity breaks. I'll propose it with the contracts Protocol PR below.
- **A replayed old "still good" arriving after an "out of X" flip** sets `have` again. Exactly-once reply handling belongs in the bot (dedupe on Telegram `update_id`); noted for @bot.
- **One invalid stored row makes `confirm_stocked` fail closed with `PantryRowError`**, as every other pantry call does since #12. The fix is re-running the seed loader.

## Revert note

Migration 2 belongs to contracts and stays if this code is reverted. The pre-PR-2 code ignores `next_ask_on`, so after a code-only revert:
- postponed staples would be asked about again at once;
- seed-reminded staples (no purchase date) would never be asked about.

If you revert, clear the column: `UPDATE pantry_item SET next_ask_on = NULL`.

## Gates run

- **Seams:** `.context/seams/lane-b-pantry.md` Revisions 4 and 5. Tier **high**.
- **RED:** by test-writer (`tw-pantry-confirm`), then one spec-watchdog pass (FIX-THEN-FREEZE; 4 fixes, no re-audit). Code-review repairs also went through test-writer.
- **Post-GREEN mutation checks:** 12/12 killed, on a committed tree with every mutant asserted applied. They cover:
  - max→assignment and max→min;
  - growth mutants;
  - the reminder keyed on the seed row's date;
  - clearing only on a strictly later purchase;
  - the ask date ignoring `next_ask_on`, or taking the 90% point first;
  - an empty `reminded`, the seed spelling, and the CLI line dropped.
- **CI five** locally on 3.11: `uv sync --locked`, ruff check, ruff format --check, mypy, pytest (673 passed), plus lint-imports (2 kept).
- **/simplify:** 3 tidy-ups, no defects.
- **/steadows-code-review:**
  - `/code-review` plus the lens reviewer found 14 issues, confidence-scored: 1 HIGH fixed, 7 MEDIUM (6 fixed, 1 PR-body note), 5 LOW (4 fixed, 1 accepted limitation), and 1 discarded as pre-existing.
  - Split: 11 product, 1 test coverage, 0 test infrastructure.
  - The Codex single-agent sweep (`task-muiqw2eu-y6ji7z`, brief `docs/prompts/pantry-pr2-adversarial-review.md`) found **1 MEDIUM**, reproduced and fixed: the ON CONFLICT + rowcount replay check logged a purchase without restocking under `PRAGMA count_changes`. It was reverted to main's read-then-insert, with a regression test. Nothing else was found.
- **Ultrareview:** after this PR opens.

## After merge

- **@contracts:** the one-line PR adding `confirm_stocked(name, on, plenty=False)` to the `Pantry` Protocol, plus the joint "leave `next_ask_on` empty when the current ask date wins" change (fake + real). Then close `bot-contracts-pantry-confirm-stocked`.
- **@bot:** "still good" → `confirm_stocked(name, today)`; "we have plenty" → `confirm_stocked(name, today, plenty=True)`. Dedupe replies on `update_id`.
- **Pantry PR 3:** `meals/mcp_tools.py`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_019DV3RhXumk2PyGTHgTPeX3
