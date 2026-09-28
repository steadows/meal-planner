"""Wait until nothing from the meal planner is running, so its code can be switched safely.

`deploy/uninstall.sh` runs this after booting out the launchd agents (ADR-0001, Consequences,
rollback steps 2 and 3):

1. Wait until no `meals bot` or `meals job` process is left. This catches a job the bot spawned
   just before it exited, before that job takes its lock.
2. Wait until every lock in `data/locks` can be taken at one instant: the job locks, the Chrome
   lock and the claude slots. Every `claude` child inherits its runner's slot fd, and a cart
   fill's child also the job and Chrome locks, so an orphaned `claude --chrome` still filling
   the cart keeps this step waiting although no `meals` process is left.

Each attempt is non-blocking and closes every fd before the next, so it never holds one lock
while waiting on another. Locks are released by closing, never `LOCK_UN`. While waiting, it
names what it waits on. Standard library only: it runs before the code it guards is trusted.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

PATTERN = "meals (bot|job)"
POLL_S = 2.0
# Tests only: gives each test its own process name to wait on, so a live bot on the same Mac
# can't stall it. Production never sets it.
PATTERN_ENV = "MEALS_DRAIN_PATTERN"


def meals_processes() -> list[str]:
    """Return `<pid> <command>` for each running `meals bot` or `meals job` process but this one."""
    pids = _meals_pids()
    return _describe(pids) or pids  # a pid ps no longer shows has just exited


def _meals_pids() -> list[str]:
    pattern = os.environ.get(PATTERN_ENV, PATTERN)
    found = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
    if found.returncode not in (0, 1):  # 1 is "none"; anything else means we can't tell
        raise RuntimeError(f"pgrep failed (exit {found.returncode}): {found.stderr.strip()}")
    return [pid for pid in found.stdout.split() if int(pid) != os.getpid()]


def busy_locks(lock_dir: Path) -> list[Path]:
    """Try every `*.lock` in `lock_dir` at once; return the ones held elsewhere.

    An empty list means every lock was ours at one instant (or `lock_dir` doesn't exist yet). A
    directory that can't be listed raises. All fds are closed before returning.
    """
    try:  # not Path.glob, which hides a directory it can't list
        with os.scandir(lock_dir) as entries:
            paths = sorted(Path(entry.path) for entry in entries if entry.name.endswith(".lock"))
    except FileNotFoundError:
        return []  # no lock was ever taken
    busy: list[Path] = []
    fds: list[int] = []
    try:
        for path in paths:
            fd = os.open(path, os.O_RDONLY)
            fds.append(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                busy.append(path)
    finally:
        for fd in fds:
            os.close(fd)  # never LOCK_UN: closing releases only our own hold
    return busy


def holders(path: Path) -> list[str]:
    """Return `<pid> <command>` for each process with `path` open; empty if lsof can't tell."""
    try:
        found = subprocess.run(
            ["lsof", "-t", "--", str(path)], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return []
    return _describe(found.stdout.split())


def _say(line: str) -> None:
    print(line, flush=True)


def drain(lock_dir: Path, *, poll_s: float = POLL_S, out: Callable[[str], None] = _say) -> None:
    """Return once no `meals` process is running and every lock in `lock_dir` is free.

    Each phase says what it waits on when that changes, not on every poll.
    """
    shown_pids: list[str] = []
    while pids := _meals_pids():
        if pids != shown_pids:
            for process in _describe(pids) or pids:
                out(f"waiting for: {process}")
            shown_pids = pids
        time.sleep(poll_s)
    shown_locks: list[Path] = []
    while locks := busy_locks(lock_dir):
        if locks != shown_locks:
            for path in locks:
                out(f"waiting for lock {path.name}: {_held_by(path)}")
            shown_locks = locks
        time.sleep(poll_s)


def _held_by(path: Path) -> str:
    names = holders(path)
    return "held by " + "; ".join(names) if names else "held by an unknown process"


def _describe(pids: list[str]) -> list[str]:
    if not pids:
        return []
    found = subprocess.run(
        ["ps", "-ww", "-o", "pid=,command=", "-p", ",".join(pids)],  # -ww: Linux cuts at 80
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in found.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: drain.py <lock_dir>", file=sys.stderr)
        return 2
    try:
        drain(Path(args[0]))
    except (OSError, RuntimeError) as exc:  # can't tell what's running: never report safe
        print(f"drain.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
