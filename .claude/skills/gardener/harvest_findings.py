#!/usr/bin/env python3
"""Harvest ```findings-json``` blocks from merged PRs and count fault classes.

Usage: python3 .claude/skills/gardener/harvest_findings.py [--since YYYY-MM-DD] [--json]
       (default: the last 7 days of merged PRs)

The review skills (/steadows-code-review, Codex steadows-ultrareview) end every report with a
findings-json block that gets posted to the PR. This reads PR bodies and comments, parses those
blocks, and reports fault classes by count, so /wrap Phase 6b and the gardener can bump the
"Seen" column in .brain/pm/lint-candidates.md from data instead of re-reading prose.
Blocks that fail to parse are counted and reported, never silently dropped.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

BLOCK = re.compile(r"```findings-json\s*\n(.*?)\n```", re.DOTALL)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main() -> None:
    since = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]
    prs = json.loads(
        _gh(
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            "200",
            "--search",
            f"merged:>={since}",
            "--json",
            "number,body,comments",
        )
    )
    by_class: Counter[str] = Counter()
    where: dict[str, set[int]] = defaultdict(set)
    dispositions: Counter[str] = Counter()
    bad_blocks: list[int] = []
    for pr in prs:
        texts = [pr.get("body") or ""] + [c.get("body") or "" for c in pr.get("comments", [])]
        for text in texts:
            for raw in BLOCK.findall(text):
                try:
                    findings = json.loads(raw)
                except json.JSONDecodeError:
                    bad_blocks.append(pr["number"])
                    continue
                for f in findings if isinstance(findings, list) else []:
                    cls = f.get("fault_class") or "unclassified"
                    by_class[cls] += 1
                    where[cls].add(pr["number"])
                    dispositions[f.get("disposition") or "?"] += 1
    result = {
        "since": since,
        "prs_scanned": len(prs),
        "by_class": {
            c: {"findings": n, "prs": sorted(where[c])} for c, n in by_class.most_common()
        },
        "dispositions": dict(dispositions),
        "unparseable_blocks_in_prs": sorted(set(bad_blocks)),
    }
    if "--json" in sys.argv:
        print(json.dumps(result, indent=1))
        return
    print(f"findings harvest — merged since {since}, {len(prs)} PRs scanned")
    for c, info in result["by_class"].items():
        print(f"  {info['findings']:3d}  {c:28s} PRs {', '.join(f'#{n}' for n in info['prs'])}")
    if not by_class:
        print("  (no findings-json blocks yet: the review skills emit them from 2026-09-28)")
    print(f"  dispositions: {dict(dispositions)}")
    if bad_blocks:
        print(f"  ⚠ unparseable findings-json in PRs: {sorted(set(bad_blocks))}")


if __name__ == "__main__":
    main()
