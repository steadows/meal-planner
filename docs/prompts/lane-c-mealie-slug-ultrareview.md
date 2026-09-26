# Post-PR ultrareview brief: mealie_slug follow-up (GitHub PR #16, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
Review `git diff origin/main...HEAD` on branch `feat/mealie` in this working tree. HEAD is the PR
head, including this brief's own commit, which is docs only. The PR description is reproduced
verbatim below.

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. The diff is small
  (~120 lines, 4 files), but it touches a **trust boundary**: `RecipeOption.mealie_slug` is a
  default-deny field that only trusted code may set under `contracts.TRUSTED`. It must never become
  settable from Claude's output or from scraped web content. Per the skill's risk override, size
  the fleet at the Max tier.
- **REVIEW ONLY:** do not edit, commit or push anything.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `.github/` and `.context/seams/`. Never read `.env`, `data/` or `seed/`. Never
  contact a real Mealie, Telegram or Meijer.
- **Prior context:**
  - `.context/seams/lane-c-mealie.md` (the "Revised by review" section and the mealie_slug note win).
  - The pre-PR review's findings and fixes, in the commit messages
    (`git log --format='%h %s%n%b' origin/main..HEAD`).
  - `docs/prompts/lane-c-mealie-slug-adversarial-review.md`, which lists every first-pass finding
    with its disposition.
  - Don't re-raise a finding those already fixed or dispositioned, unless the fix or the
    disposition is wrong.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), evidence (run probes
  where cheap) and a concrete fix. Say explicitly if nothing material is found.

---

## PR #16 description (verbatim)

## What this changes

A recipe the planner picks from Mealie now carries its Mealie ID (`mealie_slug`). Publishing the week can then put it straight on the meal plan. Without the ID, the only way was to re-import its web page, which duplicates the recipe in Mealie. A recipe typed into Mealie by hand has no web page at all, so it couldn't be published. This is the follow-up to contracts #13, which added the field as trusted-only.

## How

- `get_recipe` (`meals/mealie_client.py:201`) builds the `RecipeOption` from Mealie's scraped fields through the normal typed constructor, where the trusted-only field is refused. It then adds only the slug with `RecipeOption.model_validate(option.model_dump() | {"mealie_slug": slug}, context={TRUSTED: True})`, the same idiom as `meals/fakes/mealie.py`. No `model_copy`, which would skip validation.
- `mealie_slug` is the slug the caller asked for, as the `MealieClient` Protocol says (`contracts.py:241`). It is not the response body's own `slug` field.

## Tests (RED via test-writer + one spec-watchdog pass: trust boundary, high tier)

- `test_get_recipe_maps_mealie_fields_onto_recipe_option`: gains `mealie_slug == "fajitas"`.
- `test_get_recipe_sets_mealie_slug_to_the_slug_asked_for` (new): the body's slug differs from the path, and the path wins.
- `test_get_recipe_fills_gaps_for_a_hand_entered_recipe`: a hand-entered recipe (no URL) still gets its slug.
- `test_a_slug_may_mix_case_digits_underscores_and_hyphens`: echoes the slug exactly, case and all. This also runs every character class of the client's allowlist through contracts' `MealieSlug`, so a contracts-side tightening fails CI here instead of crashing the planner.
- The integration test asserts `mealie_slug == imported` against a live Mealie. It skips without `MEALIE_TOKEN`.

The watchdog's mutants: the body slug, a lowercased slug and a plain constructor are each caught. `model_copy` is an equivalent mutant, since the slug is already allowlisted before the request.

## Review gates

- `/simplify`: clean. I skipped one suggestion, a shared helper in `contracts.py`; that file belongs to the contracts lane, and the idiom is 2 lines.
- `/steadows-code-review`: 10 first-pass findings scored. Two LOWs were fixed by the refactor commit: the first version built from a dict under TRUSTED, which lost mypy-plugin field checking and trusted the whole scraped payload. The out-of-lane items went to pm (planner catches only KeyError → filed as the Mealie error-contract item; plan_state loaders must pass TRUSTED → in wiring's notes). The Codex single-agent sweep found no new MEDIUM+ findings.

## Test plan

- [x] `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`, `pytest`: 622 passed
- [x] New assertions fail against the pre-fix implementation (4 failed, 139 passed at the test commit)
- [ ] `/steadows-ultrareview` (Codex) after the PR opens
- [ ] Live check once Lane A is up: `uv run pytest -m integration tests/test_mealie_client_integration.py`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01ALcSaD79VSbQxospA5Kd7e
