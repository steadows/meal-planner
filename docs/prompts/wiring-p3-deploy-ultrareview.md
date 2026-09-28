# Post-PR ultrareview brief: wiring P3, launchd deploy (GitHub PR #29, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL branch. Don't use
GitHub.**
- There's no GitHub access in this session, and none is needed.
- The change set is branch `feat/wiring-p3` against `origin/main` in this working tree.
- HEAD is the PR head, including this brief's own commit (docs only).

## Rules

**Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a cache under
`/tmp`, and the sandbox has denied that before. These are pre-written for you:
- `.context/reviews/p3-diff.patch`: `git diff origin/main...HEAD` for everything in the PR;
- `.context/reviews/p3-commits.txt`: the commit list;
- `.context/reviews/p3-pr-body.md`: the PR description.

**Don't run** `uv`, `pytest`, `python`, `launchctl`, or any script in `deploy/`.
- The scripts manage real launchd agents on Steve's Mac, and the others write caches the sandbox may deny.
- The suite is green: 65 deploy tests, 1107 in all, on Python 3.11. ruff, strict mypy, shellcheck and
  import-linter are clean.
- Reason from the code.
- Throwaway probes are allowed only under `/tmp`: system tools such as `bash -n`, a `pgrep`/`ps` read, or the
  system Python with no repo imports. Never `launchctl bootstrap` or `bootout`.
- **A denied write is a stop-and-report, never a workaround.**

**Fleet size: Max tier.** This is the FIRST pass on this PR, so a full fleet is allowed. It's **concurrency, locking
and process lifecycle**:
- a rollback drain that must see every `meals bot|job` process and then hold every flock in `data/locks` at one
  instant (including locks an orphaned `claude --chrome` child inherited);
- launchd agent lifecycle: bootstrap, bootout, RunAtLoad, KeepAlive, calendar coalescing;
- signal paths through `caffeinate -i` → `uv run` → python.

Per the skill's risk override, size the fleet at the Max tier.

**REVIEW ONLY.** Don't edit, create, commit or push anything in the repo. Don't spawn nested sub-fleets beyond the
skill's own design.

**Data boundary (docs/AUTONOMOUS_WORK.md §3).** Read only:
- `deploy/`, `meals/`, `tests/`, `docs/`, `pyproject.toml`, `.github/`;
- `CLAUDE.md`, `.context/seams/P3.md`, `.context/reviews/p3-*`.

Never read `.env`, `data/` or `seed/`. Never contact Telegram, Mealie or Meijer.

## Standards
**Pre-existing and authoritative:**
- `docs/adr/ADR-0001-runtime-model.md` (accepted by Steve):
  - Decision (around :43);
  - the Jobs table (around :82);
  - the Job contract, including the stale-lock alert and the Stuck-run pointer (around :114);
  - Orphaned Chrome child (around :184);
  - Consequences → rollback steps 1–4 (around :236);
  - Risk Matrix #10, #11, #19, #20;
  - Integration Test Points → "Plists".
- `meals/jobs.py`, `meals/background.py` (`spawn_job`), `meals/__main__.py`, `meals/claude_runner.py` (`_slot`,
  `hold_fds`), `meals/config.py`.

**The author's claims** (verify them, don't defer to them):
- every comment and the README in `deploy/`;
- `.context/seams/P3.md`, the design contract: D1–D13 plus review decisions D14–D21;
- the tests.

## Prior review
Dispositions not to re-raise unless the disposition itself is wrong:
- `.context/reviews/p3-code-review.md` is the full pre-PR report. It has 18 first-pass findings and 3 Codex sweep
  findings. All were fixed RED-first or documented, and 4 were filtered at a score of 25; the reasons are listed.
- `.context/reviews/p3-codex-sweep.md` is the single-agent sweep.
- `docs/prompts/wiring-p3-deploy-adversarial-review.md` lists the first-pass findings.
- The PR's "Decisions for Steve" are deliberate. Challenge one only if it breaks an ADR rule or can lose data or
  double a cart:
  - the nudge fires stay at Sat 16–21 per the ADR;
  - production runs from the main checkout, and the scripts refuse linked worktrees;
  - the real install needs `--activate`.
- **Skipped on purpose:**
  - an unanchored `PATTERN` (the ADR's pattern; a false match only waits, and names what it waits on);
  - a second full clone isn't detected (documented).

## Callers and consumers that don't exist yet
Judge the files against them:
- `meals bot` (bot lane, P4.1): the bot agent's command;
- the cart lane's Chrome lock, expected at `data/locks/*.lock` (ADR rollback step 3);
- P4.4 lifecycle acceptance, which will run uninstall with real stub subprocesses;
- P4.8 activation on Steve's Mac, from `~/meal-planner` on `main`.

## Output
- Findings, each with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), category, evidence (cite code; probes only
  where cheap and sandbox-safe) and a concrete fix.
- Report only findings that survive the skill's verification. Say explicitly if nothing material is found.
- State which files you actually read.
