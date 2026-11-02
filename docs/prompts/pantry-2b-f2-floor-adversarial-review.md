# Pantry PR 2b — pre-PR adversarial review: the F2 week floor (pantry's half)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out. A
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR
ultrareview gate, not this one). Your job is to find issues that a thorough first-pass review
MISSED, not to repeat what was already found. Findings only. Do not edit code, and write no files.

You cannot run git in this sandbox. The diffs are pre-written:
- `.context/reviews/pantry-2b-f2-floor.diff`: the change under review (7a9dbe9..b6fc54f, limited to
  meals/pantry.py, meals/mcp_tools.py, tests/test_pantry_confirm.py, tests/test_pantry_parity.py).
- `.context/reviews/contracts-f2-floor-half.diff`: the contracts lane's half of the same joint PR
  (3abb7f9..7a9dbe9). It was reviewed and swept by Codex separately. Read it as context, the
  reference behaviour, not as review scope.

Data boundary: you may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT
read `.env`, `data/` or `seed/`. You may run the tests with `.venv/bin/python -m pytest -q` (not
`uv run`), and never with `-m integration`.

## Deference rule
The dividing line for trusting a comment, docstring, or design-doc section is not code-vs-docs,
it's authored-by-this-diff vs pre-existing. Anything the diff under review adds or changes is a
claim by the author and must be verified against the code before you clear a finding on its
authority. Anything that predates the diff is the standard the code is measured against. Where they
disagree, the pre-existing contract wins unless the diff explicitly and defensibly amends it. Do
not generalize this to "discount documentation": a pre-existing design doc is frequently the only
thing that makes a finding judgeable at all.

Here the authority for the rule is Steve's ruling of 2026-11-01 ("still good" always holds the next
ask off for at least a week), as written in the seam map (`.context/seams/lane-b-pantry.md`,
Revision 7) and in the `Pantry` Protocol docstrings in `meals/contracts.py` (contracts' half). The
fake in `meals/fakes/pantry.py` is the reference behaviour the real pantry must match.

## Not a false positive
Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the triggering condition hasn't
occurred yet. If the code as written produces the wrong behavior once that condition is real, it's
a live finding now, scored on what the code does, not deferred as "future risk."

## The rule
**The rule (both halves):**
- `pushed = on + (max(interval, 7) if plenty and interval is not None else 7)`. Unchanged.
- `confirm_stocked` stores `max(item.next_ask_on, pushed)`, or `pushed` when `next_ask_on` is
  NULL. It always writes a date now; the keep-or-leave-NULL branch is gone.
- The ask date (`_ask_date`) is the later of `next_ask_on` and the 90% point, whichever are known.
  None when neither is known.
- The accepted consequence: a stored `next_ask_on` can no longer bring an ask EARLIER than the 90%
  point.
- Unchanged: the latest purchase clears `next_ask_on`, and a back-dated one leaves it alone. That
  is why the floor survives the back-dated purchase F1 found. The seed reminder only sets
  `next_ask_on` with no purchase, so there's no 90% point to compare. Status becomes `have`, the
  interval is never touched, and no purchase is logged.

## The done contract (what this change promised)
### Done contract — PR 2b, pantry's half of the joint F2 PR

**Done means.** "Still good" and "have plenty" always hold the next pantry question back until the
push, a learned interval can still move it later, and the real pantry matches contracts' fake item
for item, and every AC below is proven.

**Acceptance criteria.** The proof column names the test, as frozen at RED in 6ea3c4b, which is 3ac2195
after the rebase onto contracts' af9564f. Full suite after the rebase: 9 fail (the 8 changed tests
plus parity, each on its assertion), 1054 pass.

| AC | Given / When / Then | Proof |
|---|---|---|
| AC-1 | Given an item whose 90% point is later than the push, when "still good" comes on day 0, then `next_ask_on` = day 7 is stored and the item isn't due until its 90% point | `tests/test_pantry_confirm.py::test_confirming_stores_the_later_of_the_stored_ask_and_the_push`, `tests/test_pantry_confirm.py::test_a_confirmed_item_is_not_due_until_the_later_of_the_stored_push_and_its_90_percent_point` |
| AC-2 | (The named fault, F1.) Given coffee, interval 60, bought day -10, and "still good" on day 0: when a back-dated purchase on day -14 re-learns the interval as 4 (90% point day -6), then coffee isn't due on days 0 to 6 and is due on day 7 | `tests/test_pantry_confirm.py::test_a_back_dated_purchase_that_shortens_the_interval_never_brings_the_ask_inside_the_week` |
| AC-3 | Given the same setup, when a back-dated purchase on day -30 re-learns the interval as 20 (90% point day 8), then coffee isn't due on day 7 and is due on day 8: the interval still moves the ask LATER | `tests/test_pantry_confirm.py::test_a_back_dated_purchase_that_lengthens_the_ask_past_the_week_still_moves_it_later` |
| AC-4 | Given a stored `next_ask_on` and a 90% point, when `staples_due` runs, then the ask date is the later of the two: in either order, and on a tie | `tests/test_pantry_confirm.py::test_a_staple_with_next_ask_on_is_due_from_the_later_of_that_day_and_its_90_percent_point`; the due-on-the-90%-point half: `tests/test_pantry_confirm.py::test_a_confirmed_item_is_not_due_until_the_later_of_the_stored_push_and_its_90_percent_point` |
| AC-5 | Plenty: an interval under a week waits a week; a longer one waits one interval; no interval waits a week; a stored later `next_ask_on` is kept | `tests/test_pantry_confirm.py::test_confirming_stores_the_later_of_the_stored_ask_and_the_push` (plenty rows), `tests/test_pantry_confirm.py::test_confirming_never_changes_the_interval` |
| AC-6 | Replays: the same reply on the same day changes nothing the second time; plenty then still good, repeated on one day, keeps the plenty push; and the stored date is the later of the stored date and the push, so a later push is never undone | `tests/test_pantry_confirm.py::test_confirming_twice_for_the_same_day_changes_nothing_the_second_time`, `tests/test_pantry_confirm.py::test_plenty_and_still_good_replayed_in_turn_on_one_day_change_nothing_after_the_first`, and the "later next_ask_on kept" / "earlier next_ask_on moved" rows of `tests/test_pantry_confirm.py::test_confirming_stores_the_later_of_the_stored_ask_and_the_push` |
| AC-7 | Given the fake and the real pantry with the same items, when the same replies run, then they return the same items, the same `list_items` and the same `staples_due` | `tests/test_pantry_parity.py::test_the_fake_and_the_real_pantry_agree_on_the_same_replies`, run after the rebase |

Every AC-1/AC-5 row reads the row back and checks the stored `next_ask_on` column, which is never
NULL now (the rule that a write must be shown to change something).

**Also in this PR, carried from Revision 6 (added to the contract after the done check, 2026-11-02):**
- `meals/mcp_tools.py`: `_PantryTools` deleted, and `OpenPantry`/`_opened` typed against
  `contracts.Pantry`. Steve's ruling relayed by pm 2026-09-26: "Delete `_PantryTools` in 2b once #24
  is on main". #24 is on main. Proof: mypy and the existing `tests/test_mcp_tools.py`.
- The `SqlitePantry.confirm_stocked` docstring mirrors the Protocol in full, lookup and datetime
  rules included (review finding F5, FIX NOW in Revision 6a). Proof: read against
  `meals/contracts.py` `Pantry.confirm_stocked`.
- Parity rows from Revision 6: plenty under a week (yeast), a datetime `on` at UTC-10, and a stored
  name with spaces (honey). They widen AC-7 to the lookup and datetime rules the fake already had.
  Proof: AC-7's test.
- The test count is 1064, one more than at RED: contracts' e023d6e adds
  `tests/test_contracts.py`'s schema test. The frozen pantry tests are unchanged since RED (ef5a3b1
  after the second rebase; `git diff ef5a3b1 HEAD -- tests/` is empty).

**Out of scope (the fence):**
- The fake, the Protocol docstrings, the test_contracts rows: contracts, the same joint PR, on its
  own branch.
- "Still good" growing the interval: not planned (Steve, 2026-09-26).
- Bot wiring of the replies and the `update_id` dedupe: bot (Lane D).
- The `claude_runner` pantry-run shape: contracts, when bot asks.
- `save_product` for the cart: post this PR, when the cart lane asks.
- A `source` parameter on `log_purchase`: contracts, on bot's revisit trigger.
- PLAN.md:223 wording ("pushes the next ask back a week" already reads as the floor): pm, only if
  pm wants "at least" added.

**Budget.** One PR: the joint F2 PR, which pantry opens. Review stop rule: stop when two
consecutive post-PR Codex convergence rounds find no product defect. Later list:
- A purchase dated BEFORE the "still good" day but NEWER than `last_purchased` counts as the latest
  purchase. It clears `next_ask_on`, and a short re-learned interval can then put the ask inside
  the week. Contracts' Protocol text (af9564f) documents it: "only a newer purchase clears that".
  The only producer of `log_purchase` is the "ordered" path, so this needs a late, out-of-order
  purchase. Contracts' review scored it 25. · needs-ruling (Steve: does a late purchase dated
  before a "still good" override it?)
- A computed ask date on the `PantryItem` contract (so no caller has to work it out again). Contracts
  added the field description instead (7a9dbe9). · needs-design (nothing reads the field yet)

## Bin every finding
## 2. The three-bin finding rule (every review gate, every round)

Every finding from `/simplify`, `/steadows-code-review`, the Codex sweep, `/steadows-ultrareview`,
`/steadows-verify` or a seams pass gets exactly one bin:

| Bin | When | What happens |
|---|---|---|
| **FIX NOW** | It breaks an AC or the team DoD (§3), or it is a correctness/security/data-loss defect in code this story adds or changes | Fixed in this PR, or in a follow-on PR **under the same story**. The story stays open until it is fixed |
| **DROP** | A nit, style, or speculative future concern; OR pre-existing and outside this story's surface; OR it sits in the story's Out of scope list | One line in the PR summary. **No ticket** |
| **LATER** | A real defect that can't be fixed here, for exactly one reason: `post-PoC` · `other-owner` (another owner's infra or lane) · `needs-ruling` (a Steve product call) · `needs-design` (ARCHITECTURE-first) | One line on the story's **Later list**, with its reason. **No ticket from the lane** |

**Severity doesn't pick the bin; scope does.** A HIGH on this story's surface is FIX NOW. A HIGH outside it
that is pre-existing is LATER, tagged `poc-blocking`.

"It would need its own RED suite" or "the PR is getting big" is **not** a LATER reason. It means a follow-on
PR under the same story (Steve: fix it, don't enumerate it; keep PRs small).

A finding that lands in the Out of scope list is DROP: one line, no argument.

## What the first-pass review already caught (do not repeat)
Fixed in b6fc54f (re-check the fix only if you think it's wrong):
- LOW · meals/pantry.py:374 and :445 — the load_seed docstring and _update_from_seed comment said
  the ask date is never moved; now they say a stored next_ask_on is never rewritten. FIX NOW, fixed.
- LOW · meals/pantry.py:256 (confirm_stocked docstring) — the misplaced "duplicate inserts are
  rejected" sentence. FIX NOW, fixed.
- LOW · meals/pantry.py:287 — the log line now shows the stored floor as well as the effective
  ask date. FIX NOW, fixed.
Binned DROP:
- LOW · meals/mcp_tools.py:141 — the confirm_stocked tool returns PantryItem.next_ask_on, which is
  now a floor, not the ask date. The root fix landed in contracts' 7a9dbe9 (a Field description
  that reaches every tool's output schema). Nothing mounts the tools yet.
- LOW · meals/pantry.py:135 — rollback: main treats a stored floor as the ask date, so after a
  rollback an item confirmed under the new code is asked weekly until its next purchase. Extra
  asks, no data loss. Goes in the PR body as a rollback note.
- LOW · meals/pantry.py:256 — confirm_stocked's docstring mirrors the Protocol in full (by design:
  review finding F5).
Filtered out (score under 50): a stale "plenty" dated before last_purchased storing a late floor
(pre-existing; the base stores the same date) · the real/fake rule duplication (by design;
parity-guarded) · no confirm row starting from 'have' (false: the datetime test and the parity
script both do).
On the Later list already: a purchase dated before the "still good" day but newer than
last_purchased clears the floor (needs-ruling) · a computed ask date on the contract (needs-design).

## Your mandate
1. SKIP anything already covered above. No duplicates.
2. Focus on what automated reviewers commonly miss:
   - Subtle logic errors that only show under specific input combinations (ties, None
     interval, None last_purchased, plenty with interval exactly 7, a stored next_ask_on equal to
     the push, a datetime `on`).
   - Cross-file interaction: a reader of next_ask_on or of the ask date anywhere in meals/
     (planner, bot, jobs, plan_state, mcp_tools, the seed loader) that still assumes the old
     meaning (a set next_ask_on IS the ask date).
   - Divergence between the real pantry and the fake on any input the parity script doesn't
     cover.
   - State transitions: confirm_stocked, log_purchase (latest vs back-dated), flip_status, and
     load_seed, in every order, against the floor rule.
   - Error handling gaps where a failure would pass silently.
3. For each finding give: File, Line, Category (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), Severity (CRITICAL | HIGH | MEDIUM), Bin
   (FIX NOW | DROP | LATER with reason), Finding, Evidence (why it is real, not speculative: the
   input that triggers it and what happens), Fix.
4. If the first pass was thorough and you find nothing material, say so explicitly. Do not invent
   findings.
