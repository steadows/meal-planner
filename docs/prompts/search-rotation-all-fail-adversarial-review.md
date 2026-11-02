# Adversarial review brief: search rotation-pool all-fail rule (pre-PR, follow-on to PR #32)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a single-agent review is
a hard requirement. Find what a thorough first pass MISSED; do not repeat it. Findings only. Do not edit code.

## Data boundary (code yes, data no)
Read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`, `.context/reviews/search-rotation-all-fail*`,
`.context/seams/search-rotation-all-fail.md`. Never `.env`, `data/`, `seed/`. No git on this machine: the diff is
pre-written at `.context/reviews/search-rotation-all-fail.diff` (`git diff origin/main...HEAD`).

## What changed
`meals/planner.py` `_rotation_pool` now raises `MealieUnavailable` when no rotation favourite loaded and at least one
`get_recipe` raised `MealieUnavailable` (before: only when every slug raised it, so a deleted favourite next to
unavailable ones gave a quiet empty pool). Docstrings of `_rotation_pool` and `propose()` updated; the error list became
one `last_unavailable` variable. Tests in `tests/test_planner.py`. Context: `meals/contracts.py` (`MealieUnavailable`),
`meals/fakes/mealie.py`, `meals/jobs.py` (`_propose`, `_catch_all`, `safe_exc_info`). Note: @mealie is wrapping the real
client's errors in `MealieUnavailable` on another branch, merging after this; treat that as a near-certain condition.

## Deference rule
The dividing line for trusting a comment, docstring, or design-doc section is authored-by-this-diff vs pre-existing.
Anything this diff adds or changes is a claim by the author and must be verified against the code. Anything that
predates the diff is the standard. Where they disagree, the pre-existing contract wins unless the diff explicitly and
defensibly amends it.

## Not a false positive
Code that mishandles a real, reachable future condition is a live finding now, scored on what the code does.

## The done contract
## Done contract

**Done means.** When no rotation favourite loaded and at least one failed with `MealieUnavailable` (even if others
were deleted), `propose()` raises instead of quietly planning a week with no favourites; stale tags alone (all
deleted, or no favourites) still plan from web finds — and every AC below is proven.

| AC | Given / When / Then | Proof |
|---|---|---|
| AC-1 | Given every rotation favourite raises `MealieUnavailable`, when `propose()` runs, then it raises `MealieUnavailable` naming the count, chained, and Claude is never called (unchanged) | `tests/test_planner.py::test_propose_fails_loudly_when_mealie_serves_no_favorite[all-unavailable]` |
| AC-2 | Given one favourite is deleted (KeyError) and the rest raise `MealieUnavailable`, when `propose()` runs, then it raises the same way | `tests/test_planner.py::test_propose_fails_loudly_when_mealie_serves_no_favorite[deleted-and-unavailable]` |
| AC-3 | Given every favourite is deleted, or there are none, when `propose()` runs, then it does not raise and plans from web finds | `tests/test_planner.py::test_all_deleted_favorites_are_not_a_mealie_outage`, `tests/test_planner.py::test_an_empty_pool_gives_only_new_finds` |
| AC-4 | Given at least one favourite loads, when others are unavailable, then they are skipped with a WARNING (unchanged) | `tests/test_planner.py::test_a_favorite_mealie_cannot_serve_is_skipped_with_a_warning` |

**Budget.** One PR. Stop rule: two consecutive post-PR convergence rounds with no product defect. Later list:
- `MealieUnavailable` class docstring (`meals/contracts.py`) says "fail loudly when every call fails", now narrower than the planner's rule (no call succeeds and any raised it) · other-owner (contracts; sent 2026-11-02 for its bot-asks wording pass)

## Scope fence
| item | where it lives instead |
|---|---|
| A partial-outage threshold (raise when "most" favourites fail) | not planned (a loaded favourite means Mealie can serve) |
| Stopping early after repeated timeouts | not planned (DROP, re-checked 2026-11-02) |
| `list_by_tag` wrapping | @mealie / @contracts (Later list of PR #32) |


Acceptance → proof (done check): AC-1..AC-4 all pass (`.context/reviews/search-rotation-all-fail-evidence.txt`).

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


A finding in the Scope fence is DROP: one line, no argument.

## Already caught by the first pass (do not repeat)
- `meals/contracts.py:240`, LOW (50): the `MealieUnavailable` docstring still says "fail loudly when every call
  fails", narrower than the new rule. LATER other-owner (contracts backlog).
- `tests/test_planner.py:422`, LOW (50): no test pins served + deleted + unavailable together (non-empty pool with both
  failure kinds). DROP (no AC broken; speculative regression).
- Filtered (< 50): real client doesn't raise yet (0, pre-existing, @mealie); one corrupt recipe + stale tags fails every
  week (25, intended trade-off; a single always-failing favourite did so under the old rule too); message counts
  deleted slugs (25, literally true); one served favourite disables the check (0, pre-existing, Scope fence); test
  doesn't vary order (25).

## Your mandate
1. Skip anything above.
2. Focus on: input combinations (duplicate slugs from `list_by_tag`, a slug that is both deleted and in `unavailable`,
   one favourite total); cross-file effects (what `jobs` does with the raise; what Steve sees; retries); error-handling
   gaps; callers of `propose()`; test validity (does each test fail for the right reason on regression?).
3. Each finding: File, Line, Category (security | correctness | concurrency | compatibility | cascade-failure |
   state-machine), Severity (CRITICAL | HIGH | MEDIUM), Bin, Finding, Evidence, Fix.
4. If you find nothing material, say so explicitly. Do not invent findings.
