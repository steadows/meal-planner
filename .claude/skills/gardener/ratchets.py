#!/usr/bin/env python3
"""Count the patterns the gardener is ratcheting down in meal-planner. Run from anywhere in the repo.

Usage: python3 .claude/skills/gardener/ratchets.py [--json]

Each metric should only ever go DOWN. The gardener compares this run against the previous row
of .brain/pm/gardener-log.md and reports the delta; a metric going UP is a new occurrence that
spread (the "virus" pattern), and is the first thing to look at.

Ported 2026-11-01 from rosie's gardener (itself ported from enterprise_research_dashboard's).
rosie's Neo4j driver metric is dropped: meal-planner has no graph.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


ROOT = Path(_git("rev-parse", "--show-toplevel") or ".")
# The live vault and the memory index belong to the MAIN worktree, not this checkout.
_common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir") or ROOT / ".git")
MAIN_ROOT = _common.parent
MEMORY_INDEX = (
    Path.home() / ".claude/projects" / str(MAIN_ROOT).replace("/", "-") / "memory/MEMORY.md"
)
REPO_QUEUE = MAIN_ROOT / ".brain/pm/lint-candidates.md"
CAP = 800
CODE_DIRS = ("meals", "deploy")


def _py_files() -> list[Path]:
    return [p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts]


def _count(pattern: str, files: list[Path]) -> int:
    rx = re.compile(pattern)
    return sum(len(rx.findall(p.read_text(errors="ignore"))) for p in files)


def metrics() -> dict[str, int | float]:
    code = _py_files()
    queue_text = REPO_QUEUE.read_text() if REPO_QUEUE.exists() else ""
    return {
        "noqa_ble001": _count(r"#\s*noqa:\s*BLE001", code),
        "suppress_exception": _count(r"suppress\(\s*Exception\s*\)", code),
        "raw_env_reads": _count(r"os\.(?:environ\.get|getenv)\(", code),
        "type_ignore": _count(r"#\s*type:\s*ignore", code),
        "files_over_800_lines": sum(
            1 for p in code if p.read_text(errors="ignore").count("\n") > CAP
        ),
        "lint_candidates_queued": len(re.findall(r"\|\s*queued", queue_text)),
        "lint_candidates_landed": len(re.findall(r"\*\*landed", queue_text)),
        "memory_index_kb": round(MEMORY_INDEX.stat().st_size / 1024, 1)
        if MEMORY_INDEX.exists()
        else 0,
    }


if __name__ == "__main__":
    m = metrics()
    if "--json" in sys.argv:
        print(json.dumps(m))
    else:
        for k, v in m.items():
            print(f"{k:30s} {v}")
