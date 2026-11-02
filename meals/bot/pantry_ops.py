"""Apply pantry intents in arrival order (PLAN.md Phase 4; ADR-0001, Runtime concurrency).

Each Application owns LatestWrites. Its lock covers the newest-ticket check, pantry write and
record together. Expired work writes nothing; only buy_next_time is supported in this phase.
A write's echo uses the pantry's returned name and never describes next_ask_on as an ask date.
"""

import itertools
import logging
import sqlite3
import threading
import time

from meals.contracts import Intent, Pantry
from meals.rollup import name_key

logger = logging.getLogger(__name__)


class LatestWrites:
    """Per-Application arrival tickets and the newest successful write to each canonical item."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets = itertools.count(1)
        self._writes: dict[str, int] = {}

    def ticket(self) -> int:
        """Take the next arrival ticket on the event loop, before starting background work.

        It never takes the write lock, so the event loop never waits on a worker's pantry write.
        """
        return next(self._tickets)

    def flip(self, name: str, *, pantry: Pantry, ticket: int, apply_by: float) -> str:
        """Resolve aliases before checking ordering, and record only successful writes."""
        unknown = f"I don't know {name}. Which pantry item did you mean?"
        with self._lock:
            if time.monotonic() > apply_by:
                return f"I ran out of time to update {name}. Please send it again."
            item = pantry.get_item(name)
            if item is None:
                return unknown
            key = name_key(item.name)
            if ticket < self._writes.get(key, 0):
                return f"A newer message already set {item.name}."
            if time.monotonic() > apply_by:
                return f"I ran out of time to update {item.name}. Please send it again."
            written = pantry.flip_status(item.name, "buy_next_time")
            if written is None:
                return unknown
            self._writes[key] = ticket
            return f"{written.name} is on the list."


def apply(
    intents: tuple[Intent, ...],
    *,
    pantry: Pantry,
    ticket: int,
    apply_by: float,
    latest: LatestWrites,
) -> tuple[str, ...]:
    """Return one result line per intent, writing only supported, timely, current intents."""
    lines: list[str] = []
    for intent in intents:
        if intent.kind != "pantry_flip" or intent.args.to != "buy_next_time":
            lines.append("I can't do that yet.")
            continue
        try:
            line = latest.flip(intent.args.item, pantry=pantry, ticket=ticket, apply_by=apply_by)
        except sqlite3.Error as exc:
            logger.warning("couldn't write a pantry item (%s)", type(exc).__name__)
            line = f"I couldn't update {intent.args.item} right now. Please send it again."
        lines.append(line)
    return tuple(lines)
