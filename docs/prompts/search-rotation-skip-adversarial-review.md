# Adversarial review brief: search rotation-pool skip (pre-PR)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR ultrareview
gate, not this one). Your job is to find issues that a thorough first-pass review MISSED, not to
repeat what was already found. Findings only. Do not edit code.

## Data boundary (code yes, data no)

You may read `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, and `.context/reviews/`,
`.context/seams/` in this worktree. Do NOT read `.env`, anything under `data/`, or `seed/`. Do not
contact Mealie, Telegram or Meijer, and do not run `claude`. You have no git on this machine: the
diff is pre-written for you.

## The diff to review

`.context/reviews/search-rotation-skip.diff` = `git diff origin/main...HEAD` on branch
`feat/search-rotation-skip` (4 commits: RED, Codex GREEN, /simplify refactor, a docstring fix).
Files worth reading in full after the change: `meals/planner.py` (`_rotation_pool`, `propose`),
`tests/test_planner.py`, `tests/test_search.py`. Context you will need: `meals/contracts.py`
(`MealieUnavailable`, `MealieClient`), `meals/fakes/mealie.py`, `meals/mealie_client.py`,
`meals/jobs.py` (`_propose`, `_catch_all`, `safe_exc_info`), `tests/conftest.py` (`live_claude`).
The seam map is `.context/seams/search-rotation-skip.md`.

## Deference rule

The dividing line for trusting a comment, docstring, or design-doc section is not code-vs-docs,
it's authored-by-this-diff vs pre-existing. Anything the diff under review adds or changes is a
claim by the author and must be verified against the code before you clear a finding on its
authority. Anything that predates the diff is the standard the code is measured against. Where
they disagree, the pre-existing contract wins unless the diff explicitly and defensibly amends it.
Do not generalize this to "discount documentation": a pre-existing design doc is frequently the
only thing that makes a finding judgeable at all.

## Not a false positive

Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the triggering condition hasn't
occurred yet. If the code as written produces the wrong behavior once that condition is real, it's
a live finding now, scored on what the code does, not deferred as "future risk." Note: the real
`HttpMealieClient` wrapping its errors in `MealieUnavailable` is being built now by another lane
(`feat/mealie-unavailable`); treat that as a near-certain future condition.

## The done contract: what this change promised

## Done contract

**Done means.** One Mealie favourite that errors no longer sinks the whole Saturday proposal, a
Mealie that serves *no* favourite stops the run with a plain error instead of a silent web-only
week, and `pytest -m integration` alone no longer spends Claude usage on search's two live
tests — and every AC below is proven.

| AC | Given / When / Then | Proof |
|---|---|---|
| AC-1 | Given two rotation favourites, one loads and one raises `MealieUnavailable`, when `propose()` runs in mix mode, then the loaded one is offered, the failing slug is not in the prompt, and a WARNING names the failing slug | `tests/test_planner.py::test_a_favorite_mealie_cannot_serve_is_skipped_with_a_warning` |
| AC-2 | Given every rotation favourite raises `MealieUnavailable`, when `propose()` runs, then it raises `MealieUnavailable` naming the count, chained `from` a client error, and Claude is never called | `tests/test_planner.py::test_propose_fails_loudly_when_mealie_serves_no_favorite` |
| AC-3 | Given no favourite raised `MealieUnavailable` for *every* call (no rotation slugs; all deleted; deleted + unavailable mixed), when `propose()` runs, then it does not raise and plans from web finds alone | `tests/test_planner.py::test_no_served_favorite_is_not_a_mealie_outage` (parametrized: all KeyError, KeyError + unavailable) and, for no rotation slugs, the existing `tests/test_planner.py::test_an_empty_pool_gives_only_new_finds` (the "no-favorites" row was dropped by /simplify as a duplicate; a planted bug that drops the `unavailable and` guard fails it with IndexError) |
| AC-4 | Given `MEALS_LIVE_CLAUDE` is unset, when `uv run pytest -m integration tests/test_search.py tests/test_planner.py -rs` runs, then both live tests skip with the `live_claude` reason and no `claude` process starts | Evidence: the `-rs` skip output, pasted in the PR. The fixture itself is unit-tested by contracts (`tests/test_claude_runner.py::test_live_claude_tests_skip_unless_meals_live_claude_is_1`) |

**Budget.** One PR. Review stop rule: stop when two consecutive post-PR single-agent Codex
convergence rounds find no product defect. Later list: empty.

## Scope fence
| item | where it lives instead |
|---|---|
| Wrapping the real client's httpx / body errors in `MealieUnavailable` (`meals/mealie_client.py`) | @mealie, step 2 of the error-contract note; waits on Steve's go |
| `list_by_tag` failures | not planned: outside the contract, and already loud (raises raw, fails `propose()`) |
| How `jobs` reports a failed propose (Telegram wording, message-only logging) | @wiring; already covered by `tests/test_jobs_weekend.py:837` |
| The fallback "still have some?" pantry question | search, next PR (`feat/search-pantry-fallback`) |
| `planner.apply_reply` | search, waits on @bot's typed Intent args |
| Planner observability (a log line after a successful run; `list_by_tag` and prefs reads) | not planned for this PR (handoff deferred item) |


### Acceptance → proof (from the done check)

| AC | proof | status |
|---|---|---|
| AC-1 | `tests/test_planner.py::test_a_favorite_mealie_cannot_serve_is_skipped_with_a_warning` | pass |
| AC-2 | `tests/test_planner.py::test_propose_fails_loudly_when_mealie_serves_no_favorite` | pass |
| AC-3 | `tests/test_planner.py::test_no_served_favorite_is_not_a_mealie_outage[all-deleted]`, `[deleted-and-unavailable]`, `tests/test_planner.py::test_an_empty_pool_gives_only_new_finds` | pass |
| AC-4 | `.context/reviews/search-rotation-skip-evidence-ac4.txt` (both live tests skip with `MEALS_LIVE_CLAUDE` unset; no `claude -p` from this worktree) | pass |

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


A finding that lands in the Out of scope list (the Scope fence above) is DROP: one line, no argument.

## What the first-pass review already caught (after confidence scoring)

- `meals/planner.py:210`, LOW (score 50): the real `HttpMealieClient.get_recipe` never raises
  `MealieUnavailable` yet, so the skip and the all-fail raise only run against the fake. Bin: DROP
  (scope fence: @mealie's wrap, in progress).
- `meals/planner.py:213`, LOW (score 50): the loud raise fires only when every fetch is
  unavailable; a partial outage (e.g. 9 of 10) or a mix of KeyError and unavailable silently
  shrinks or empties the pool with WARNING lines only. Bin: DROP (deliberate design per the seam
  map's altitude decisions and the `MealieUnavailable` docstring).
- `meals/planner.py:200` (the `_rotation_pool` docstring) and `:203` (`list_by_tag`), LOW (score
  50): a `list_by_tag` failure still raises raw httpx/pydantic errors, not `MealieUnavailable`.
  Bin: LATER, other-owner (contracts/mealie); `list_by_tag` is in the scope fence.
- `meals/planner.py:201` and `tests/test_planner.py:422`, LOW (score 50): docstrings said "a
  KeyError is Mealie answering 404"; the client also raises KeyError for a malformed slug. Bin:
  FIX NOW, already fixed in commit 1e10672 ("an unknown slug").

Filtered out (score < 50), for your awareness only: no short-circuit after repeated failures
(each waits the client timeout, score 25); the outage-vs-one-recipe split belongs in the client
(score 25); list vs counter state (score 25).

## Your mandate

1. SKIP anything already covered by the findings above. No duplicates.
2. Focus on these blind-spot categories that automated reviewers commonly miss:
   - Subtle logic errors that only manifest under specific input combinations (duplicate slugs
     from `list_by_tag`; a slug that is both deleted and unavailable; one favourite total)
   - Cross-file interaction bugs (how `jobs._propose` / `_catch_all` / `safe_exc_info` handle
     the new raise; what Steve sees in Telegram)
   - Security issues hiding in "normal" code (does any new log line or message carry unvetted
     text?)
   - Error handling gaps where failures cascade silently
   - Backward compatibility breaks (callers of `propose()`)
   - Test validity: does each new test fail for the right reason if the behaviour regresses?
3. For each finding, provide:
   - **File:** path
   - **Line:** number (if applicable)
   - **Category:** security | correctness | concurrency | compatibility | cascade-failure | state-machine
   - **Severity:** CRITICAL | HIGH | MEDIUM
   - **Bin:** FIX NOW | DROP | LATER (post-PoC | other-owner | needs-ruling | needs-design)
   - **Finding:** What the first-pass review missed and why it matters
   - **Evidence:** Why this is a real risk, not speculative
   - **Fix:** Concrete recommendation
4. If the first-pass review was thorough and you can't find material issues, say so explicitly.
   Do not invent findings to justify your existence.
