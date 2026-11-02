# Post-PR ultrareview brief — the joint F2 week-floor PR (GitHub PR #34, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
The branch is `feat/pantry-f2-ask-date` in this working tree. The PR head is e7ed4c6, not counting
this brief's own commit. The PR description is reproduced verbatim below. Everything you need is
local.

- **The diff, without git:** git is not available in your sandbox on this Mac. The full PR diff
  (`git diff origin/main...e7ed4c6`) is pre-written at `.context/reviews/pr34.diff`. Files:
  - pantry's half: `meals/pantry.py`, `meals/mcp_tools.py`, `tests/test_pantry_confirm.py`,
    `tests/test_pantry_parity.py`;
  - contracts' half: `meals/contracts.py`, `meals/fakes/pantry.py`, `tests/test_contracts.py`;
  - `docs/prompts/pantry-2b-f2-floor-*.md` are this PR's Codex briefs (GREEN and the pre-PR
    sweep).
  - No dependency, schema or migration change.
- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. Size it to the
  diff per the skill (about 860 changed lines, two thirds of them tests). Give these full weight:
  - **State machine and replay safety:** `next_ask_on` changes meaning from "the ask date" to "a
    floor under the ask date". Check every transition against the floor in every order:
    `confirm_stocked` (still good / plenty), `log_purchase` (latest vs back-dated vs same-day
    replay), `flip_status`, and `load_seed` (first run and re-run). Replies are delivered at least
    once.
  - **Cross-file readers:** every reader of `next_ask_on` or of the ask date anywhere in `meals/`
    (planner, jobs, plan_state, mcp_tools, the seed loader, bot code if present) that still
    assumes the old meaning.
  - **Fake/real parity:** `FakePantry` is the reference, `SqlitePantry` must match it. Look for
    inputs the parity script doesn't cover where the two diverge (the one known divergence,
    interval re-learning on a second purchase date, is by design and documented in the parity
    test's docstring).
  - **The contract surface:** the `Pantry` Protocol docstrings and the new
    `PantryItem.next_ask_on` schema description, which reaches every pantry MCP tool's output
    schema.
- **REVIEW ONLY:** do not edit, commit or push anything. Write no files.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `uv.lock`, `.github/`, `.context/seams/` and `.context/reviews/`. Never read
  `.env`, `data/` or `seed/`, or any real Telegram, Mealie or Meijer content.
- **Prior context:**
  - `.context/seams/lane-b-pantry.md`, Revision 7 at the top: the rule, the done contract
    (AC-1..AC-7), the out-of-scope fence and the Later list. It's this PR's own design, so verify
    it against Steve's ruling as quoted there.
  - The pre-existing contract: `docs/PLAN.md`'s Pantry rules, and `meals/db.py`.
  - The pre-PR reviews: pantry's (in the PR body below, with its findings-json) and contracts'
    (summarised in the PR body; the Codex sweep on each half found 1 and 0 new issues).
  - Don't re-raise a finding already fixed, dropped with a reason, or on the Later list, unless
    the fix or the reason is wrong.
- **Probes:** run them where cheap and read-only: `.venv/bin/python -m pytest -q
  tests/test_pantry_confirm.py tests/test_pantry_parity.py tests/test_contracts.py
  tests/test_mcp_tools.py` (not `uv run`; never `-m integration`), or `.venv/bin/python -c`
  driving `SqlitePantry` over an in-memory database next to `FakePantry`. **If a command is denied,
  continue statically; it is not a reason to stop.**
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), evidence (the input
  that triggers it and what happens) and a concrete fix. Bin each one FIX NOW / DROP / LATER (with
  reason) per the done contract's three-bin rule, which the PR body follows. Say explicitly if
  nothing material is found.

---

## PR #34 description (verbatim)

## Summary

**Phase 3 / Lane B (pantry) + Lane 0 (contracts) — the joint F2 PR: the week floor.**

Steve's ruling, 2026-11-01: when he tells the bot an item is "still good", it always waits **at least a week** before it asks about that item again. ("When you tell the bot 'the coffee is still good', should it always wait at least a week before it asks about the coffee again?" — "yes".)

The rule, in both the fake and the real pantry:
- `pushed = on + (max(interval, 7) if plenty and interval is not None else 7)` (unchanged).
- `confirm_stocked` stores `max(stored next_ask_on, pushed)`, so it always writes a date.
- `next_ask_on` is now a **floor**: the ask date is the later of `next_ask_on` and the 90% point. A `buy_next_time` flag still makes an item due at once.

Why one PR: `tests/test_pantry_parity.py` compares `FakePantry` with `SqlitePantry` item by item, so either half alone turns main red. Each lane committed only its own files.

- **contracts** (`meals/contracts.py`, `meals/fakes/pantry.py`, `tests/test_contracts.py`): 8aedd2b, 5975bad, 3abb7f9 (the first F2 cut), then 18a5378 RED, 9083241 GREEN (Codex), c047226 /simplify, 2deac52, af9564f, e023d6e, 7a9dbe9. `PantryItem.next_ask_on` now carries a schema description saying it's a floor, so every pantry MCP tool's output schema says so.
- **pantry** (`meals/pantry.py`, `meals/mcp_tools.py`, `tests/test_pantry_confirm.py`, `tests/test_pantry_parity.py`): `_ask_date` is the later of the two dates; `_postponed_ask` stores the max; `_PantryTools` deleted (`confirm_stocked` is on `contracts.Pantry` since #24); the `confirm_stocked` docstring mirrors the Protocol; the MCP tool description states the floor; the log line shows the effective ask date and the stored floor.

Earlier pantry commits on this branch (034ff05, 382e67a, 089e053) built Revision 6, the rule before Steve's ruling. The Revision 7 commits on top supersede them.

## Acceptance → proof

Pantry half (seam map `.context/seams/lane-b-pantry.md`, Revision 7). All rows: `uv run pytest` at HEAD, 1064 passed; merged with origin/main e8ef70c, 1131 passed.

| AC | proof | status |
|---|---|---|
| P-AC-1 a "still good" under a later 90% point stores day 7, and the item isn't due until its 90% point | `tests/test_pantry_confirm.py::test_confirming_stores_the_later_of_the_stored_ask_and_the_push`, `tests/test_pantry_confirm.py::test_a_confirmed_item_is_not_due_until_the_later_of_the_stored_push_and_its_90_percent_point` | pass |
| P-AC-2 (the named fault F1) a back-dated purchase that re-learns a shorter interval never brings the ask inside the week | `tests/test_pantry_confirm.py::test_a_back_dated_purchase_that_shortens_the_interval_never_brings_the_ask_inside_the_week` | pass |
| P-AC-3 a back-dated purchase that moves the 90% point past the week still moves the ask later | `tests/test_pantry_confirm.py::test_a_back_dated_purchase_that_lengthens_the_ask_past_the_week_still_moves_it_later` | pass |
| P-AC-4 `staples_due` uses the later of `next_ask_on` and the 90% point, either order and a tie | `tests/test_pantry_confirm.py::test_a_staple_with_next_ask_on_is_due_from_the_later_of_that_day_and_its_90_percent_point`; due on the 90% point: `...::test_a_confirmed_item_is_not_due_until_the_later_of_the_stored_push_and_its_90_percent_point` | pass |
| P-AC-5 plenty: under a week waits a week, longer waits one interval, none waits a week, a later stored date is kept | `tests/test_pantry_confirm.py::test_confirming_stores_the_later_of_the_stored_ask_and_the_push` (plenty rows), `tests/test_pantry_confirm.py::test_confirming_never_changes_the_interval` | pass |
| P-AC-6 replays: the same reply twice changes nothing; plenty then still good keeps the plenty push; a later push is never undone | `tests/test_pantry_confirm.py::test_confirming_twice_for_the_same_day_changes_nothing_the_second_time`, `tests/test_pantry_confirm.py::test_plenty_and_still_good_replayed_in_turn_on_one_day_change_nothing_after_the_first`, the stored-date rows of `...::test_confirming_stores_the_later_of_the_stored_ask_and_the_push` | pass |
| P-AC-7 the fake and the real pantry return the same items, `list_items` and `staples_due` | `tests/test_pantry_parity.py::test_the_fake_and_the_real_pantry_agree_on_the_same_replies` | pass |

Contracts half (`.brain/drafts/contracts-f2-floor-review-2026-11-02.md`):

| AC | proof | status |
|---|---|---|
| C-AC-1 | `tests/test_contracts.py::test_fake_pantry_next_ask_on_is_a_floor_under_the_interval_rule` (rice row) | pass |
| C-AC-2 | `tests/test_contracts.py::test_fake_pantry_next_ask_on_is_a_floor_under_the_interval_rule` (tahini row) | pass |
| C-AC-3 | `tests/test_contracts.py::test_fake_pantry_confirming_always_stores_the_push_as_a_floor`, `::test_fake_pantry_confirming_stores_the_later_of_stored_and_pushed`, `::test_fake_pantry_still_good_never_pulls_the_ask_earlier` | pass |
| C-AC-4 | `tests/test_contracts.py::test_fake_pantry_still_good_holds_a_week_when_the_interval_later_shrinks` | pass |
| C-AC-5 | the `Pantry` class and `confirm_stocked` docstrings and the `PantryItem.next_ask_on` description (af9564f, 7a9dbe9) | pass |
| C-AC-6 | `tests/test_contracts.py::test_pantry_item_schema_says_next_ask_on_is_a_floor` | pass |

## Gates

| Gate | Pantry half | Contracts half |
|---|---|---|
| seams | Revision 7 (2026-11-01), tier HIGH | ordinary |
| RED | ef5a3b1 via `tw-pantry-f2-floor` + one `spec-watchdog` pass: FREEZE (7 deliberate bugs each caught) | 18a5378 |
| GREEN | 1f5baa8 by Codex (gpt-6-astra, xhigh) | 9083241 by Codex |
| /simplify | 2d2249f | c047226 |
| /steadows-code-review | done check + /code-review + lens + scoring; fixes b6fc54f; Codex single-agent sweep (gpt-6-astra, xhigh): no new findings (222 tests, 3,456 boundary combinations, 720 call orderings) | done; Codex sweep 1 new, fixed |
| CI checks locally | ruff, format, mypy, lint-imports clean; 1064 passed (1131 merged with main) | clean |

## Review findings, binned (pantry half)

DROP:
- The `confirm_stocked` tool returns `next_ask_on`, now a floor: the root fix is contracts' schema description (7a9dbe9).
- The `confirm_stocked` docstring repeats the Protocol in full: by design (finding F5).
- Test tidiness in frozen tests (duplicate rows across two tables, a repeated coffee setup).
- **Rollback note.** `main` treats a stored `next_ask_on` as the ask date. If this PR is rolled back after "still good" replies were stored, those items are asked about weekly until their next purchase. Extra asks only, no data loss.

## Later list (no tickets)

- A purchase dated before the "still good" day but newer than `last_purchased` counts as the latest purchase and clears the floor; a short re-learned interval can then ask inside the week. Documented in the Protocol ("only a newer purchase clears that"). Needs a late, out-of-order purchase. · needs-ruling
- A computed ask date on the `PantryItem` contract. · needs-design (nothing reads the field yet)

Filtered by scoring (score under 50, not acted on): a stale "plenty" dated before `last_purchased` storing a late floor (pre-existing, 0) · the real/fake rule duplication (by design, parity-guarded, 25) · "no confirm row starts from `have`" (false, 0).

```findings-json
[{"gate": "code-review", "severity": "LOW", "fault_class": "stale-comment-after-semantics-change", "file": "meals/pantry.py:374", "summary": "load_seed docstring and _update_from_seed comment said the ask date never moves", "disposition": "fix-now"},
 {"gate": "code-review", "severity": "LOW", "fault_class": "misplaced-docstring-claim", "file": "meals/pantry.py:256", "summary": "confirm_stocked docstring said duplicate inserts are rejected", "disposition": "fix-now"},
 {"gate": "code-review", "severity": "LOW", "fault_class": "log-drops-stored-value", "file": "meals/pantry.py:287", "summary": "confirm_stocked log line dropped the stored floor", "disposition": "fix-now"},
 {"gate": "code-review", "severity": "LOW", "fault_class": "raw-field-misread-as-derived", "file": "meals/mcp_tools.py:141", "summary": "tool returns next_ask_on, now a floor; root fixed by the contract's schema description", "disposition": "drop"},
 {"gate": "code-review", "severity": "LOW", "fault_class": "rollback-semantics", "file": "meals/pantry.py:135", "summary": "a rollback to main reads stored floors as the ask date", "disposition": "drop"},
 {"gate": "code-review", "severity": "LOW", "fault_class": "docstring-duplication", "file": "meals/pantry.py:256", "summary": "confirm_stocked docstring mirrors the Protocol in full (by design)", "disposition": "drop"}]
```
