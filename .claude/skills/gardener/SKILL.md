---
name: gardener
description: >-
  Harness gardener for meal-planner. Measures the ratchets (patterns that must only go down), reads
  what the harness hooks actually did, harvests structured review findings from merged PRs, then
  lands ONE queued lint/CI/hook rule as a small PR and retires the memories it replaces. Run by
  hand with /gardener (no schedule is installed). Never merges, never files tickets, never asks
  Steve: it produces a PR (or a landed hook rule) and one log line.
user-invocable: true
---

# /gardener — the harness improves itself

The codebase is the agents' strongest memory: they copy whatever pattern is already there. The
gardener keeps what's there worth copying, and turns lessons into enforcement so agents need to
read less (Steve, 2026-09-28: autonomy through trust in the harness, not through more approval
gates). Ported 2026-11-01 from rosie's gardener, itself ported from enterprise_research_dashboard's
(Steve: "make sure ... we've got the /gardener skill installed"). The gardener is @pm's job.

Tools live in this directory. Run them; don't re-derive them.

| Tool | Answers |
|---|---|
| `uv run python .claude/skills/gardener/ratchets.py --json` | Counts in meal-planner's code (`meals/`, `deploy/`) that must only go down |
| `~/.claude/bin/hook-report 7` | Which hook rules fired, repeated, or never fired (missing on some machines: see step 2) |
| `uv run python .claude/skills/gardener/harvest_findings.py --json` | Fault classes in this week's merged PRs' review findings |

Two queues, one catalog:
- meal-planner's code rules: `.brain/pm/lint-candidates.md` (in the main checkout's live vault,
  `~/meal-planner/.brain/`).
- shared harness rules (hooks, Codex dispatch, shell and git habits, the brain engine):
  `~/.claude/hooks/lint-candidates-harness.md`. The dashboard's and rosie's gardeners work this
  list too.
- Fault-class names: the catalog at the top of the shared file. Map each harvested `fault_class`
  to a catalog name (add a new class there first if none fits).

## Steps

Write `.brain/` files in the live vault (`~/meal-planner/.brain/`, the main checkout), never a
worktree's snapshot. Do code work ONLY in your own worktree (`~/meal-planner-gardener`, branch
`feat/pm-gardener-<date>-<slug>`, created from `origin/main`). Never check out branches in the
shared main checkout.

1. **Measure.** Run `ratchets.py --json`. Compare with the last row in `.brain/pm/gardener-log.md`.
   Any metric that went UP is a pattern spreading: find the commits that added it (`git log -S`),
   and name them in the log line.
2. **Read the harness.** Run `hook-report 7`. If it is missing on this machine, write "hook-report
   missing" in the log line and go on.
   - HOT rule → agents keep making that mistake. Fix the cause upstream if you can: the skill, doc
     or script that led them to the wrong command.
   - REPEATS → the deny message isn't landing. Rewrite the message, or turn the deny into a safe
     rewrite.
   - COLD for 30+ days → note it as a deletion candidate in the log. Don't delete on one quiet week.
3. **Harvest findings.** Run `harvest_findings.py --json`. For each fault class with findings, bump
   **Seen** on its row in the right queue, or add a row if the class is new AND a machine could
   catch it. Append a Log line to each queue you touched.
4. **Land one rule.** Pick the queued row with the highest Seen whose Effort is S, and whose
   mechanism doesn't need an architecture decision (ADR or module map). Before you start a row from
   the SHARED list, set its Status to `in progress (meal-planner gardener <date>)`, so the
   dashboard's and rosie's gardeners don't pick it too.
   - meal-planner code rule: implement it as a ratchet that starts at today's count (allowlist or
     per-file-ignores), so it can't stall in-flight work. Follow the normal workflow
     (`docs/AUTONOMOUS_WORK.md`): RED first, Codex GREEN, `/simplify`, `/steadows-code-review`,
     `brain announce "about to PR"`, open the PR, then the Codex ultrareview (small fleet). **Open
     the PR and stop. Never merge** (merge waits for Steve's go, merge commit only). Mark the row
     `PR #<n>`.
   - harness rule (hooks in `~/.claude`): land it directly, with test cases added to
     `~/.claude/hooks/tests/` and every `*_cases.py` file passing; mark the row `landed <date>`.
5. **Retire memories.** For each row marked landed, find the memories it enforces. Add
   "✅ Enforced by <mechanism> (lint-candidates #<n> / H-<n>)" to the top of each, and remove them
   from the MEMORY.md index. The files stay on disk.
6. **Log.** Append one row to `.brain/pm/gardener-log.md` with the ratchet numbers, the deltas,
   hot/cold rules, findings harvested, the rule landed or PR opened, and memories retired. Commit
   the vault with `brain commit` from the main checkout (path-scoped, secret-scanned), check
   ahead/behind against `origin/main`, then push.

## Guardrails

- One rule per run. Small PRs merge; big ones stall.
- Never lower a ratchet's allowlist by editing the count upward. If a metric rose, the log says so
  and the next rule targets it.
- No approval prompts, no tickets. If something genuinely needs Steve's ruling (an ADR change, for
  instance), write it in the log line under "needs ruling" and move on.
- If the tools fail (gh auth, a missing file), log the failure and finish. Don't improvise a
  substitute measurement.
- meal-planner rules that apply to every change: no Claude credit lines in commits or PRs; never
  commit `.env` or any secret; never `gh pr merge --admin`. Only the contracts lane edits
  `meals/contracts.py`, `meals/db.py`, `meals/config.py`, `meals/claude_runner.py` and
  `meals/fakes/`: a rule that needs one of them goes to contracts as a request
  (`brain dm @contracts`). `pyproject.toml` and `tests/conftest.py` take added lines only.
