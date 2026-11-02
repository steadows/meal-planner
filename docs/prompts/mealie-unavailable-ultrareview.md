# Post-PR ultrareview brief: get_recipe → MealieUnavailable (GitHub PR #36, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
git is not available to you here either, so the diff is pre-written:

- `.context/reviews/mealie-unavailable.diff` — `git diff origin/main...HEAD` on branch
  `feat/mealie-unavailable` at PR head `cce2aad`. This brief's own commit, which is docs only, comes
  after it and is not in the diff.
- `.context/reviews/mealie-unavailable-commits.txt` — every commit message on the branch, with the
  RED / GREEN / repair history.
- `.context/reviews/mealie-unavailable-pr-body.md` — the PR description, verbatim.

The working tree is at the PR head, so read any file in full from it.

- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. The diff is small
  (one production file, ~30 changed lines, plus tests and briefs), but it is a **secrets
  surface**: the whole point of the change is that the Mealie bearer token can never reach the
  message of the `MealieUnavailable` this client raises, which callers log and send to Telegram. Per
  the skill's risk override (auth/secrets), size the fleet at the Max tier.
- **REVIEW ONLY:** do not edit, commit or push anything.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only `meals/`, `tests/`, `docs/`,
  `pyproject.toml`, `.github/`, `.context/seams/` and `.context/reviews/`. Never read `.env`,
  `data/` or `seed/`. Never contact a real Mealie, Telegram or Meijer. Do not run
  `pytest -m integration`. Use `.venv/bin/python -m pytest`, `.venv/bin/ruff`, `.venv/bin/mypy` for
  probes; write no files outside your own scratch.
- **The contract:** `.context/seams/mealie-unavailable.md` — Done means, AC-1 to AC-8 (AC-5 and
  AC-6 amended after the pre-PR review), the Out of scope fence (findings that land there are DROP),
  the Later list, New seams and Altitude decisions.
- **Prior context:**
  - `.context/reviews/mealie-unavailable-code-review.md` — the pre-PR review: every first-pass
    finding with its score and bin, the Codex sweep's finding, and the discards.
  - `.context/reviews/mealie-unavailable-simplify.md` — the /simplify pass.
  - `docs/prompts/mealie-unavailable-adversarial-review.md` — the pre-PR Codex sweep brief.
  - Don't re-raise a finding those already fixed or binned, unless the fix or the bin is wrong.
- **Where to push hardest:**
  - Any path by which the token (or any other text Mealie controls) still reaches `str(err)`,
    `repr(err)`, the planner's `str(err)` WARNING, or `jobs` reporting — through the status, the
    method, the path, the slug or the class name.
  - Any Mealie or network failure inside `get_recipe` that still escapes as something other than
    `KeyError` / `MealieUnavailable`, and any programming error that is now wrongly wrapped.
  - Whether `set_meal_plan`, `list_by_tag` and `import_url` really keep their old errors.
  - Cross-file: `meals/planner.py` `_rotation_pool` and `meals/jobs.py` `safe_exc_info` /
    `_catch_all` as consumers. (The planner's all-unavailable guard gap is already on the Later list,
    being fixed by the search lane in its own PR; don't re-raise it.)
- **Bin every finding** per `~/.claude/rules/common/done-contract.md` §2: FIX NOW · DROP · LATER
  (post-PoC | other-owner | needs-ruling | needs-design). Scope picks the bin, not severity.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), bin, evidence (run
  probes where cheap) and a concrete fix. Say explicitly if nothing material is found. Also say,
  for each finding, whether it is a product defect or a test-infrastructure defect.
