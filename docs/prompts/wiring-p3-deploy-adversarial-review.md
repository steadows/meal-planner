# Adversarial review brief: wiring P3 (launchd deploy), pre-PR

You are a senior adversarial code reviewer working ALONE. Do NOT spawn sub-agents or fan out: a single-agent
review is a hard requirement (the multi-agent fleet belongs to the post-PR ultrareview gate, not this one). Your
job is to find issues a thorough first-pass review MISSED, not to repeat what was already found. Findings only.

## Hard rules
- **Nothing that writes or executes:**
  - Don't edit, create, move or revert any file in the repo.
  - Run no git commands at all.
  - **Don't run `git`, `uv`, `pytest`, `python`, `bash`/`sh` on any script in `deploy/`, or `launchctl`.** The
    sandbox denies their cache writes on this Mac, and the scripts manage real launchd agents.
- **Reason from the code.** The suite is green: 49 deploy tests, 1091 in all, on Python 3.11. ruff, strict mypy,
  shellcheck and import-linter are clean.
- **Data boundary:** read only `deploy/`, `meals/`, `tests/`, `docs/`, `pyproject.toml`, `CLAUDE.md`,
  `.context/seams/P3.md` and `.context/reviews/p3-*`. Never read `.env`, `data/` or `seed/`.
- If an action is denied, stop and report it; never work around it.

## What this change is
ADR-0001 phase P3: the files that let launchd run the meal planner on Steve's Mac. Nothing is activated before
plan step P4.8.
- `deploy/local.meals.<agent>.plist.template` ×5: the bot (KeepAlive) and four calendar jobs.
- `deploy/install.sh`:
  - `--dry-run` renders and lints;
  - `--activate` runs uninstall.sh, then copies the plists and runs `launchctl bootstrap gui/<uid>` on each.
- `deploy/uninstall.sh`: boots out the loaded agents, removes their plists, runs `deploy/drain.py`, then prints
  "safe to switch code".
- `deploy/drain.py` (stdlib only): waits until no `meals bot|job` process remains, then until every `data/locks/*.lock`
  can be flocked at one instant.
- `deploy/README.md`, including the Stuck-run procedure.

**Authority:**
- ADR: `docs/adr/ADR-0001-runtime-model.md`: Decision around :43, the Jobs table around :82, the Job contract around
  :114, Orphaned Chrome child around :184, Consequences/rollback around :236.
- Design record: `.context/seams/P3.md` (D1–D13, D11 amended).
- Tests: `tests/test_deploy.py`.

The whole diff is pre-written at `.context/reviews/p3-diff.patch`.

## Deference rule
The dividing line for trusting a comment, docstring or design-doc section isn't code versus docs. It's whether
this diff authored it or it was already there.
- **Authored by this diff:** that includes `deploy/README.md` and `.context/seams/P3.md`. Treat it as the author's
  claim, and verify it against the code before you clear a finding on its authority.
- **Pre-existing:** the ADR and `meals/`. That's the standard the code is measured against. Where they disagree,
  the pre-existing contract wins, unless the diff explicitly and defensibly amends it.

## Not a false positive
Code that mishandles a real, reachable future condition is a live finding now, scored on what the code does. Two
examples: P4.8 activation; the cart lane's Chrome lock, which isn't merged yet.

## What the first-pass review already caught (skip these; don't duplicate)
- [HIGH] deploy/uninstall.sh:35. It drains `$ROOT/data/locks` of the checkout it runs from, not the ROOT the loaded
  `local.meals.*` agents were installed from. From a lane worktree it can say "safe" while production's orphaned
  `claude --chrome` holds production's locks.
- [MEDIUM] deploy/install.sh:18. `--activate` bakes whatever checkout it runs from as ROOT, with no check that it's
  the production checkout (not a linked git worktree).
- [MEDIUM] deploy/drain.py:43. pgrep's exit code is ignored, so exit 2/3 gives empty output and reads as "no
  processes" (fail-open).
- [MEDIUM] deploy/uninstall.sh:28. Any `launchctl print` failure is read as "not loaded", and EUID 0 is never
  refused. Under sudo (`gui/0`) the bootout is skipped, the plists are removed, and safe is reported.
- [MEDIUM] deploy/local.meals.sat_nudge.plist.template:77. The last fire is Sat 21:00, but the nudge needs 3 h after
  the proposal and the window runs to Sun 08:00, so a proposal delivered after 18:00 is never nudged.
- [MEDIUM] deploy/uninstall.sh:25-32. It prints only "stopped X". A run with nothing loaded looks the same as one
  where the glob matched fewer than five templates.
- [LOW] deploy/drain.py:28. PATTERN is unanchored, so any argv containing `meals job` stalls the drain.
- [LOW] deploy/drain.py:55 / uninstall.sh:35. The drain only sees `$ROOT/data/locks`, so a `CLAUDE_LOCK_DIR`
  override or a Chrome lock placed elsewhere is missed.
- [LOW] tests/test_deploy.py:460. The corrupt-root test asserts only `code == 1`.
- [LOW] architecture-plan.html:295. P3 task statuses are stale.
- [LOW] deploy/install.sh:17, uninstall.sh:11. `cd "$(dirname "$0")" && pwd` breaks with an exported CDPATH.
- [LOW] deploy/README.md:9. "restarted 30 s after it exits" misstates ThrottleInterval.
- [LOW] deploy/install.sh:29. `readlink … || true` leaves a blank zone in the refusal message.
- [LOW] deploy/README.md:49-50. No guidance for when launchd can't exec a stale baked uv path (`launchctl print`,
  system log).

## Your mandate
1. SKIP anything already covered above.
2. Focus on the blind spots automated reviewers miss:
   - **launchd semantics:**
     - StartCalendarInterval Weekday numbering;
     - coalescing on wake;
     - RunAtLoad at bootstrap;
     - what bootout does to a running job and to processes it spawned in their own session (`spawn_job` uses
       `start_new_session=True`);
     - KeepAlive with a missing `meals bot` subcommand.
   - **Signal paths:** `caffeinate -i` → uv → python.
   - **The drain's two phases:**
     - races between them;
     - the gap between `pgrep` and `ps`;
     - lock files created after the sweep starts;
     - `O_RDONLY` flock on files other processes opened `O_RDWR`;
     - flock semantics across fork and exec.
   - **Shell under bash 3.2 and `set -euo pipefail`:**
     - an unmatched glob;
     - `! grep` under `set -e`;
     - sed escaping of baked paths;
     - `command -v` returning an alias or function.
   - **Cross-file assumptions:**
     - `meals/__main__.py` (JOB_LOG, `job` subcommand, the exit codes a missing `.env` produces under launchd);
     - `meals/background.py` `spawn_job` (argv and cwd);
     - `meals/jobs.py` (lock names, the stale-alert text);
     - `meals/config.py` (PROJECT_ROOT, `claude_lock_dir`).
   - **Test-isolation gaps:** any path by which `tests/test_deploy.py` could reach the real `launchctl`, the real
     `~/Library/LaunchAgents`, or the repo's `data/`.
3. For each finding, give:
   - **File**, **Line**;
   - **Category:** security | correctness | concurrency | compatibility | cascade-failure | state-machine;
   - **Severity:** CRITICAL | HIGH | MEDIUM;
   - **Finding;**
   - **Evidence:** a concrete sequence;
   - **Fix.**
4. If the first pass was thorough and you find nothing material, say so explicitly. Don't invent findings.

End with the list of files you actually read.
