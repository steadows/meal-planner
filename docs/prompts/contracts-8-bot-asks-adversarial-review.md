# Adversarial review brief: contracts-8 (bot's contracts asks), pre-PR

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a single-agent
review is a hard requirement (the multi-agent fleet belongs to the post-PR ultrareview gate, not this one). Your
job is to find issues a thorough first-pass review MISSED, not to repeat what was already found. Findings only.

## Hard rules
- **Nothing that writes or executes:**
  - Don't edit, create, move or revert any file in the repo.
  - Run no git commands at all (there is no usable git for you on this Mac).
  - Don't run `uv`, `pytest`, `python`, `claude` or any script. The sandbox denies their cache writes, and
    `claude` spends Steve's usage.
- **Reason from the code.** The suite is green: 1165 passed on Python 3.11 at HEAD 8d4b6e7. ruff, strict mypy and
  import-linter are clean. One live run of `claude -p` 2.1.314 with `web=False` and a schema passed (AC-12).
- **Data boundary:** read only `meals/`, `tests/`, `docs/`, `pyproject.toml`, `CLAUDE.md`,
  `.context/seams/contracts-8-bot-asks.md` and `.context/reviews/contracts-8-*`. Never read `.env`, `data/` or
  `seed/`, and no real Telegram, Mealie or Meijer content.
- If an action is denied, stop and report it; never work around it.

## What this change is
The contracts lane (sole owner of `meals/contracts.py`, `meals/db.py`, `meals/claude_runner.py`, `meals/fakes/`)
answers the bot lane's asks so bot PR 2b (the Telegram free-text reply path) can stack on this branch:
1. **Typed Intent args** (`meals/contracts.py`): `Intent` was one model with `kind: IntentKind` and untyped
   `args`. It is now a plain union of nine `XIntent(kind: Literal["x"], args: XArgs)` models, each a frozen,
   extra-forbidding `Contract`. Shared aliases `Text` (stripped, non-empty str) and `Number` (int >= 1);
   `ComponentSlot` names `Components`' fields. A plain union (JSON Schema `anyOf`), not pydantic's discriminated
   union, because the claude CLI compiles `--json-schema` with Ajv strict and rejects the `discriminator`
   keyword at startup (`.brain` research note, checked live 2026-11-02 on 2.1.314).
2. **Migration 4** (`meals/db.py`): `telegram_update(update_id INTEGER PRIMARY KEY, seen_at DATETIME NOT NULL
   DEFAULT CURRENT_TIMESTAMP)`. The bot (not in this diff) claims each update with `INSERT ... ON
   CONFLICT(update_id) DO NOTHING` (rowcount 1 = claimed) and prunes old rows in the same transaction.
3. **A tool-free run** (`meals/claude_runner.py`, `meals/fakes/claude.py`): `run(..., web=True)`; `web=False`
   passes `--tools ""` and no `--allowedTools`. For bot's parse run, whose prompt can carry web-sourced text and
   whose output drives pantry writes. Default unchanged.
4. **Docs**: `MealieClient.get_recipe`'s docstring points at the `MealieUnavailable` class docstring; three
   PLAN.md shared-contracts rows (pm granted them 2026-11-02).

The whole diff is pre-written at `.context/reviews/contracts-8-bot-asks.diff` (main 32fbfda...8d4b6e7). Read the
post-diff files in full where it helps: `meals/contracts.py`, `meals/db.py`, `meals/claude_runner.py`,
`meals/fakes/claude.py`, and their tests.

## Deference rule
The dividing line for trusting a comment, docstring or design-doc section isn't code versus docs. It's whether
this diff authored it or it was already there.
- **Authored by this diff:** the new docstrings and comments, the three PLAN.md rows, and
  `.context/seams/contracts-8-bot-asks.md`. Treat them as the author's claim, and verify them against the code
  before you clear a finding on their authority.
- **Pre-existing:** the rest of `docs/PLAN.md` (Pantry rules, Risks, the prompt-injection boundary, Definition of
  done), `docs/adr/`, and the rest of `meals/`. That's the standard the code is measured against. Where they
  disagree, the pre-existing contract wins, unless the diff explicitly and defensibly amends it.

## Not a false positive
Code that mishandles a real, reachable future condition is a live finding now, scored on what the code does. The
main example: bot PR 2b, which is not written yet, will validate Claude's JSON against an object holding these
Intents and apply them to the pantry and the plan.

## The done contract — what this change promised
Read `.context/reviews/contracts-8-contract.md` (Done means, AC-1..AC-14 with proofs, Out of scope, Budget).
The fresh-context done check filled this table:

| AC | proof | status |
|---|---|---|
| AC-1 | `tests/test_contracts.py::test_each_intent_kind_validates_to_its_variant` (10 rows) | pass |
| AC-2 | `::test_intent_args_reject_shapes_that_do_not_fit_the_kind` (28 rows) | pass |
| AC-3 | `::test_intent_kind_names_every_variant`, `::test_component_slot_names_every_components_field` | pass |
| AC-4 | `::test_intent_variants_are_frozen_and_reject_extras`, `::test_text_strips_and_refuses_blank` | pass |
| AC-5 | `::test_intent_schema_is_one_the_claude_cli_accepts` + research note (2026-11-02) | pass (static schema check; no live run used an Intent schema) |
| AC-6 | `tests/test_db.py::test_migration_4_adds_telegram_update_and_changes_nothing_else` | pass |
| AC-7 | `::test_migration_4_upgrades_a_v3_database_and_keeps_its_rows` | pass |
| AC-8 | `::test_telegram_update_claims_each_update_once` (rowcount 1/0/1; DELETE rowcount 1) | pass |
| AC-9 | `tests/test_claude_runner.py::test_argv_carries_the_required_flags` (5 rows) | pass |
| AC-10 | `::test_tool_free_run_is_retried_once` | pass |
| AC-11 | `tests/test_contracts.py::test_fake_claude_records_web` | pass |
| AC-12 | live run at HEAD 8d4b6e7, 2026-11-02T06:26:05Z, PASSED in 2.25s, claude 2.1.314 | pass |
| AC-13 | the diff, read by the done check | pass |
| AC-14 | connections note still `watch` | missing (the lane flips it when the PR opens) |

## Bin every finding
Every finding gets exactly one bin:

| Bin | When | What happens |
|---|---|---|
| **FIX NOW** | It breaks an AC or the team DoD, or it is a correctness/security/data-loss defect in code this story adds or changes | Fixed in this PR, or in a follow-on PR **under the same story**. The story stays open until it is fixed |
| **DROP** | A nit, style, or speculative future concern; OR pre-existing and outside this story's surface; OR it sits in the story's Out of scope list | One line in the PR summary. **No ticket** |
| **LATER** | A real defect that can't be fixed here, for exactly one reason: `post-PoC` · `other-owner` (another owner's infra or lane) · `needs-ruling` (a Steve product call) · `needs-design` (ARCHITECTURE-first) | One line on the story's **Later list**, with its reason. **No ticket from the lane** |

**Severity doesn't pick the bin; scope does.** A finding that lands in the Out of scope list is DROP: one line,
no argument.

## What the first-pass review already caught (skip these; don't duplicate)
Surviving the confidence filter (each verified by running pydantic on the branch):
- [LOW] meals/contracts.py:241 (`Intent` union) with meals/contracts.py:322 (`describe_rejection`, pre-existing). A bad intent fails in all nine union branches, and `describe_rejection` reports the first error, which is the first branch's `kind` mismatch. Example: a swap with `slot: "dessert"` reports "first at intents.0.PickIntent.kind: Input should be 'pick'", hiding the real `SwapIntent.args.slot` error.
- [LOW] meals/contracts.py:34 (`Number = Annotated[int, Field(ge=1)]`). `validate_claude_output` validates in lax mode, so JSON `true`, `"2"` and `2.0` become 1, 2, 2 in `PickArgs.numbers` and `SaveArgs.number`.
- [LOW] meals/contracts.py:224 (`RateArgs.rating: Literal[-1, 1]`) and `SaveArgs.add_to_week: bool`. Lax mode reads JSON `true` as rating 1 and `"yes"`/`1` as add_to_week True. (Note: `ConfigDict(strict=True)` does not stop `True` matching `Literal[1]`.)
- [LOW] meals/contracts.py:155 (`PickArgs.numbers`). Duplicates validate: `(2, 2, 2)`.

Considered and filtered (score < 50). Re-raise one only with new, concrete evidence:
- meals/db.py:90. `seen_at`'s UTC text format is documented, not enforced by a CHECK (bot's claim SQL could write another format). Same as migration 3's `job_run`; the claim SQL is bot's (Out of scope). Score 25.
- meals/db.py:92. Telegram picks a random `update_id` after a week with no updates, so a claim could collide with an old row if bot prunes after inserting. Bot's claim transaction (Out of scope). Score 25.
- meals/contracts.py:165. `SwapArgs.remove` is required, so "add X to a slot" has no swap form. Matches bot's ask; PLAN routes "add X" to `add_item`. Score 0.
- meals/contracts.py:27. `IntentKind` repeats the variants' kinds; kept deliberately, pinned by a test. Score 0.
- meals/db.py:88-96. Rolling back to v3 code locks processes out of a v4 database. Same for migrations 2 and 3; ADR-0001 accepts "no migration-aware rollback release". Score 25.
- meals/claude_runner.py:133,138-140,257. The failure warnings don't name the tool profile (none / web / chrome). Pre-existing for chrome. Score 0.

## Your mandate
1. SKIP anything already covered above.
2. Focus on the blind spots automated reviewers miss:
   - **pydantic v2 smart-mode union semantics:** can a payload validate as the wrong variant (for example args
     that fit two kinds, a `kind` that is a non-str, lax-mode coercion of `"2"` to 2 or `1.0` to 1 inside a
     union, `strict` vs lax in `validate_claude_output`)? Do frozen tuples survive JSON round-trips? Does
     `hash()` hold for every variant?
   - **The JSON schema Claude sees:** `$defs`/`$ref` shape, `const` vs `enum` for `kind`, `minLength`/`minimum`
     emitted for `Text`/`Number`, `additionalProperties: false`; anything Ajv strict (the CLI) would reject at
     startup, or that the API's structured-output subset would reject.
   - **The tool-free run:** does `--tools ""` really leave no built-in tool (argv order, empty-string argv
     handling, interaction with `--chrome`, with `--json-schema`, with the retry path, with `hold_fds`, and with
     any env or settings file that could re-add tools such as project `.claude/settings.json` or MCP config
     inherited from the cwd)? What tools could a prompt-injected parse run still reach?
   - **Migration 4:** upgrade under a concurrent opener, `user_version` handling, rollback to v3 code
     (`_check_version` refuses a newer DB), `seen_at` text format vs any cutoff the bot will compare, and
     INTEGER PRIMARY KEY rowid aliasing with Telegram's update_id range.
   - **Cross-file assumptions:** every existing caller of `Intent`, `IntentKind`, `ClaudeCall`,
     `FakeClaudeRunner.run` and `claude_runner.run` (grep `meals/` and `tests/`); positional-argument breaks
     from adding `web` last.
3. For each finding, give:
   - **File**, **Line**;
   - **Category:** security | correctness | concurrency | compatibility | cascade-failure | state-machine;
   - **Severity:** CRITICAL | HIGH | MEDIUM;
   - **Bin:** FIX NOW | DROP | LATER (post-PoC | other-owner | needs-ruling | needs-design);
   - **Finding;**
   - **Evidence:** a concrete sequence or input;
   - **Fix.**
4. If the first pass was thorough and you find nothing material, say so explicitly. Don't invent findings.

End with the list of files you actually read.
