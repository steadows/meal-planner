# Post-PR ultrareview brief: bot's contracts asks (GitHub PR #35, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL branch. Don't use
GitHub.**
- There's no GitHub access in this session, and none is needed.
- The change set is branch `feat/contracts-bot-asks` against `origin/main` (32fbfda) in this working tree.
- HEAD is the PR head, including this brief's own commit (docs only).

## Rules

**Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a cache under
`/tmp`, and the sandbox has denied that before. These are pre-written for you:
- `.context/reviews/contracts-8-ultra-diff.patch`: `git diff origin/main...HEAD` for everything in the PR;
- `.context/reviews/contracts-8-ultra-commits.txt`: the commit list;
- `.context/reviews/contracts-8-ultra-pr-body.md`: the PR description.

**Don't run** `uv`, `pytest`, `python` with repo imports, or `claude`.
- `claude` spends Steve's usage; the others write caches the sandbox may deny.
- The suite is green: 1168 passed on Python 3.11. ruff, strict mypy and import-linter are clean. Two live runs of
  `claude -p` 2.1.314 passed: the tool-free integration test, and a parse with the real Intent schema and
  `web=False`.
- Reason from the code.
- Throwaway probes are allowed only under `/tmp`, with the system Python and no repo imports (for example, a
  pydantic-free check of SQLite behaviour with the stdlib `sqlite3`).
- **A denied write is a stop-and-report, never a workaround.**

**Fleet size: Max tier.** This is the FIRST pass on this PR, so a full fleet is allowed. The risk overrides apply:
- a **schema migration** (migration 4, `telegram_update`) on the live pantry database;
- a **trust boundary**: the tool-free run (`web=False` → `--tools ""`) exists so a parse whose prompt can carry
  web-sourced text, and whose output drives pantry and plan writes, has no tools a prompt injection could use;
- a **data contract** every lane consumes: `Intent` changes from one model with untyped `args` to a union of nine
  typed models, validated from Claude's JSON.

Per the skill's risk override, size the fleet at the Max tier.

**REVIEW ONLY.** Don't edit, create, commit or push anything in the repo. Don't spawn nested sub-fleets beyond the
skill's own design.

**Data boundary (docs/AUTONOMOUS_WORK.md §3).** Read only:
- `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`;
- `CLAUDE.md`, `.context/seams/contracts-8-bot-asks.md` and `.context/reviews/contracts-8-*`.

Never read `.env`, `data/` or `seed/`. Never contact Telegram, Mealie or Meijer.

## Standards
**Pre-existing and authoritative:**
- `docs/PLAN.md`: Implementation plan (the Shared contracts table except the three rows this PR changes, and the
  Definition of done), Risks (the prompt-injection boundary), Pantry rules.
- `docs/adr/ADR-0001-runtime-model.md`: migrations are append-only; `get_db()` refuses a newer database; no
  migration-aware rollback release.
- The rest of `meals/`: `Contract`, `validate_claude_output`, `claude_runner` (`ALLOWED_TOOLS`, the env strip, the
  retry rule, `_slot`, `hold_fds`), `meals/db.py` migrations 1–3 and `migrate()`.
- `.brain` research note `claude-cli-json-schema-subset.md` is summarised in the seam map's Flags: the claude CLI
  compiles `--json-schema` with Ajv strict, rejects the `discriminator` keyword at startup, accepts `anyOf`,
  validates each StructuredOutput call client-side and re-prompts on mismatch.

**The author's claims** (verify them, don't defer to them):
- every new comment and docstring, and the three PLAN.md rows this PR changes;
- `.context/seams/contracts-8-bot-asks.md` (scope, done contract, AC table, altitude decisions, flags);
- the tests.

## Prior review
Dispositions not to re-raise unless the disposition itself is wrong:
- `.context/reviews/contracts-8-code-review.md` is the full pre-PR report: one FIX NOW (the rejection reason named
  the wrong union branch; fixed RED-first, 34d7ff1 + 27a1921), two DROPs, six filtered with reasons.
- `.context/reviews/contracts-8-codex-sweep.md` is the single-agent sweep (nothing new).
- `docs/prompts/contracts-8-bot-asks-adversarial-review.md` lists the first-pass findings.
- Deliberate decisions. Challenge one only if it breaks a PLAN or ADR rule, or lets an injected parse write:
  - `Intent` is a plain union, not pydantic's discriminated union (the CLI rejects `discriminator`);
  - `web: bool`, not a tools tuple (a tuple would let a caller pass file tools);
  - `web=False` is not the default (that would change planner/search runs, other lanes);
  - lax validation in `validate_claude_output`, as for every Contract (the CLI's Ajv check refuses `true` for an
    integer before Python sees it);
  - the claim SQL and the 48 h prune are bot's (`meals/bot/`, not in this PR).

## Callers and consumers that don't exist yet
Judge the files against them:
- bot PR 2b (`meals/bot/`): an `IntentReply` object holding `tuple[Intent, ...]`, parsed with
  `claude_runner.run(prompt, schema=IntentReply, web=False)`; the claim
  `INSERT INTO telegram_update (update_id) VALUES (?) ON CONFLICT(update_id) DO NOTHING` (claimed when rowcount
  is 1), with rows older than 48 h deleted first in the same transaction; then each Intent applied in Python
  through the `Pantry` Protocol and the planner.
- search's `planner.apply_reply(proposal, edits: Sequence[PickIntent | SwapIntent])`.
- First activation on Steve's Mac, which migrates the live pantry database from v3 to v4.

## Output
- Findings, each with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), category, evidence (cite code; probes only
  where cheap and sandbox-safe) and a concrete fix.
- Report only findings that survive the skill's verification. Say explicitly if nothing material is found.
- State which files you actually read.
