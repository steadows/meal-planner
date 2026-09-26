# Adversarial review brief: get_recipe sets RecipeOption.mealie_slug (Lane C follow-up)

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out. A
single-agent review is a hard requirement (the multi-agent fleet belongs to the post-PR ultrareview
gate, not this one). Your job is to find issues that a thorough first-pass review MISSED, not to
repeat what it already found. Findings only. Do not edit code.

## Data boundary (hard rule)
Code yes, data no. You may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. Do NOT
read `.env`, anything under `data/` or `seed/`, or any real Telegram, Mealie or Meijer content. Do
not contact a Mealie instance.

## Deference rule
The dividing line for trusting a comment, docstring, or design-doc section is not code vs docs. It
is authored-by-this-diff vs pre-existing. Anything the diff under review adds or changes is a claim
by the author and must be verified against the code before you clear a finding on its authority.
Anything that predates the diff is the standard the code is measured against. Where they disagree,
the pre-existing contract wins unless the diff explicitly and defensibly amends it. Do not
generalize this to "discount documentation": a pre-existing design doc is often the only thing that
makes a finding judgeable at all.

Pre-existing contracts that matter here:
- `meals/contracts.py:33-37`: `TRUSTED` is for the Mealie client and loaders of stored data, never
  for anything Claude produced.
- `meals/contracts.py:97-121`: `RecipeOption.mealie_slug` is default-deny. It is hidden from
  Claude's schema and rejected unless validated with `context={TRUSTED: True}`.
- `meals/contracts.py:~241`: the Protocol says `get_recipe` returns "The recipe, with `mealie_slug`
  set to `slug`". Raises KeyError for an unknown slug.
- `meals/fakes/mealie.py:50-52`: the fake uses the same idiom.
- `docs/adr/ADR-0001-runtime-model.md`: the publish step uses
  `option.mealie_slug or mealie.import_url(option.url)`.

## Not a false positive
Code that mishandles a real, reachable future condition (a version skew, a config not yet set, a
flag not yet flipped) is not a false positive just because the trigger hasn't happened yet. If the
code as written produces the wrong behavior once that condition is real, it is a live finding now.

## What the first-pass review already caught (skip these; each has file, line, severity)
1. `meals/mealie_client.py:219`, LOW: mealie_slug echoes the caller's key, not Mealie's canonical
   slug. Mealie's GET /api/recipes/{slug} also accepts a UUID, so one recipe could carry two
   mealie_slug values, and `set_meal_plan` dedups by string (line 244). No current caller passes a
   UUID. Kept per the Protocol docstring.
2. `meals/mealie_client.py:267` and `meals/contracts.py:81`, MEDIUM: two copies of the slug
   allowlist (client `_SLUG`, contracts `MealieSlug`), owned by different lanes. If contracts
   tightened MealieSlug, get_recipe would raise ValidationError, which `meals/planner.py:202`
   doesn't catch, instead of KeyError. Disposition: mitigated by
   `test_a_slug_may_mix_case_digits_underscores_and_hyphens`, which now runs every character class
   of the client's allowlist through MealieSlug in the same CI. A length-style tightening would
   slip past it.
3. `meals/mealie_client.py:204-220`, LOW: an earlier version of this diff built the option from a
   dict under TRUSTED, which lost mypy-plugin field checking. FIXED in commit 2e01616: typed
   constructor first, then only the slug is added under TRUSTED.
4. `meals/mealie_client.py:220`, LOW: TRUSTED covered the whole scraped payload. FIXED in the same
   commit.
5. `meals/mealie_client.py:217-218`, LOW: a misleading comment. Rewritten in 2e01616.
   `tests/test_claude_runner.py:947` (contracts lane) still says get_recipe uses model_copy. That
   goes to the contracts lane.
6. `meals/planner.py:202`, LOW, pre-existing, search lane: `_rotation_pool` catches only KeyError,
   so a 5xx or an odd body on one favorite fails all of propose().
7. `meals/mealie_client.py:220`, LOW: a stored WeekProposal re-read without TRUSTED would reject
   mealie_slug. By design (contracts says loaders pass TRUSTED). No loader exists yet.
8. `meals/mealie_client.py:208`, pre-existing, out of scope: negative `prepTimeSeconds` gives a
   negative hands_on_min.

## The diff to review
`git diff origin/main...HEAD` on branch `feat/mealie` (3 commits: 69e4d95 test, f7927fb fix,
2e01616 refactor). Read `get_recipe` and `_fetch_recipe` in `meals/mealie_client.py` in full, plus
`meals/contracts.py` (RecipeOption, TRUSTED, MealieSlug, validate_claude_output) and
`meals/planner.py` (`propose`, `_rotation_pool`, `_favorites`).

## Your mandate
1. SKIP anything already covered above. No duplicates.
2. Focus on the blind spots automated reviewers miss:
   - subtle logic errors under specific input combinations (e.g. the `model_dump()` then
     `model_validate` round trip: any field whose value changes, or any validator that behaves
     differently the second time);
   - cross-file interaction bugs (where a slugged RecipeOption flows next: planner → WeekProposal
     → Claude prompts, digests, `validate_claude_output`);
   - trust-boundary issues (can any Claude-produced value reach the TRUSTED call?);
   - error handling gaps where failures cascade silently;
   - backward-compatibility breaks for callers of get_recipe.
3. For each finding give: **File**, **Line**, **Category** (security | correctness | concurrency |
   compatibility | cascade-failure | state-machine), **Severity** (CRITICAL | HIGH | MEDIUM),
   **Finding**, **Evidence** (why it's real, not speculative) and **Fix**.
4. If the first pass was thorough and you find nothing material, say so explicitly. Do not invent
   findings.
