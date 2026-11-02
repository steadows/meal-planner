# Gardener log — meal-planner

One row per `/gardener` run (newest last). Metrics come from `uv run python .claude/skills/gardener/ratchets.py --json`;
each should only go DOWN. Queues: `.brain/pm/lint-candidates.md` (meal-planner code) and
`~/.claude/hooks/lint-candidates-harness.md` (shared harness).

| Date | noqa BLE001 | suppress(Exception) | raw env reads | type: ignore | files >800 lines | queued / landed | memory index KB | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-11-01 | 0 | 0 | 1 | 1 | 0 | 0 / 0 | 4.2 | Baseline (manual, @pm, on origin/main 46d170f, from branch `feat/pm-gardener` before the skill merged). Code dirs `meals/`, `deploy/`. hook-report missing on this machine (`~/.claude/bin/hook-report`). Findings harvest since 2026-09-20: 29 merged PRs, 0 findings-json blocks (meal-planner's reviews predate the block), so nothing to bump. No rule landed this run (port only). |
