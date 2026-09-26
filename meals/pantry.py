"""The pantry (PLAN.md, Pantry rules): item lookup, status flips, staples due, purchase learning.

`SqlitePantry` implements `contracts.Pantry` over the `pantry_item` and `purchase_log` tables. It
is the only module that reads or writes those rows, and the only place that maps a row to a
`PantryItem`. Callers pass in a connection from `db.get_db()`.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

from meals.contracts import PantryItem, PantryStatus

# PLAN: ask about a staple once 90% of its interval has passed. Kept as a ratio of integers so
# the due date is exact (0.9 isn't representable in binary floating point).
ASK_AT_NUMERATOR, ASK_AT_DENOMINATOR = 9, 10


def _match_key(name: str) -> str:
    return name.strip().casefold()


def _to_item(row: sqlite3.Row) -> PantryItem:
    """Map a row to the contract. Validation re-checks every field, including the meijer.com-only
    URL rule, so a bad row fails closed instead of reaching the cart."""
    fields = {key: row[key] for key in row.keys() if key != "updated_at"}
    fields["aliases"] = tuple(json.loads(row["aliases"] or "[]"))
    return PantryItem.model_validate(fields)


def _ask_date(item: PantryItem) -> date | None:
    """The day the planner should start asking about `item`: 90% of its interval after the last
    purchase, rounded up to a whole day. None when either is unknown."""
    if item.typical_interval_days is None or item.last_purchased is None:
        return None
    days = -(-item.typical_interval_days * ASK_AT_NUMERATOR // ASK_AT_DENOMINATOR)  # ceil
    return item.last_purchased + timedelta(days=days)


def _due_sort_key(item: PantryItem, on: date) -> tuple[bool, bool, int, str]:
    """Flagged first, then most days past the ask date, then name. Sorted ascending."""
    asked_from = _ask_date(item)
    days_past = (on - asked_from).days if asked_from is not None else 0
    return (
        item.status != "buy_next_time",
        asked_from is None,
        -days_past,
        _match_key(item.name),
    )


def _is_due(item: PantryItem, on: date) -> bool:
    if item.status == "buy_next_time":
        return True
    asked_from = _ask_date(item)
    return asked_from is not None and on >= asked_from


class SqlitePantry:
    """The real pantry. Satisfies `contracts.Pantry`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_items(self) -> tuple[PantryItem, ...]:
        """Every item, in insertion (id) order."""
        rows = self._conn.execute("SELECT * FROM pantry_item ORDER BY id").fetchall()
        return tuple(_to_item(row) for row in rows)

    def get_item(self, name: str) -> PantryItem | None:
        """The item called `name`, or carrying it as an alias, ignoring case and surrounding
        whitespace. An exact name match beats another item's alias. None if unknown."""
        wanted = _match_key(name)
        items = self.list_items()
        by_name = next((item for item in items if _match_key(item.name) == wanted), None)
        if by_name is not None:
            return by_name
        return next(
            (item for item in items if wanted in {_match_key(alias) for alias in item.aliases}),
            None,
        )

    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        """Staples to ask about on `on`, most overdue first (PLAN: flagged, or 90% of the interval
        since the last purchase). Capping how many to ask is the caller's job."""
        due = (
            item for item in self.list_items() if item.category == "staple" and _is_due(item, on)
        )
        return tuple(sorted(due, key=lambda item: _due_sort_key(item, on)))

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        """Set an item's status by name or alias. Returns the updated item, or None if unknown."""
        with self._write() as conn:
            item = self.get_item(name)
            if item is None:
                return None
            conn.execute(
                "UPDATE pantry_item SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, item.id),
            )
        return self._reread(item.id)

    def _reread(self, item_id: int) -> PantryItem:
        row = self._conn.execute("SELECT * FROM pantry_item WHERE id = ?", (item_id,)).fetchone()
        return _to_item(row)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, taken with BEGIN IMMEDIATE so reads inside it can't go stale
        before the write (the bot, a job and an MCP process may all write).

        Refuses a connection that already has a transaction open: committing here would commit the
        caller's pending writes too. Rolls back only the transaction it started.
        """
        if self._conn.in_transaction:
            raise sqlite3.ProgrammingError(
                "pantry writes need a connection without an open transaction; "
                "commit or roll back first"
            )
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.rollback()
            raise
        self._conn.commit()
