"""The pantry (PLAN.md, Pantry rules): item lookup, status flips, staples due, purchase learning.

`SqlitePantry` implements `contracts.Pantry` over the `pantry_item` and `purchase_log` tables. It
is the only module that reads or writes those rows, and the only place that maps a row to a
`PantryItem`. Callers pass in a connection from `db.get_db()`.
"""

import json
import logging
import math
import sqlite3
import statistics
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from pydantic import Field

from meals.contracts import Contract, MeijerUrl, PantryCategory, PantryItem, PantryStatus
from meals.rollup import name_key, require_positive

logger = logging.getLogger(__name__)

# PLAN: ask about a staple once 90% of its interval has passed. Kept as a ratio of integers so
# the due date is exact (0.9 isn't representable in binary floating point).
ASK_AT_NUMERATOR, ASK_AT_DENOMINATOR = 9, 10


class SeedItem(Contract):
    """One row of the seed CSV (the ingredient → Meijer product map): a `PantryItem` without the
    database's `id` and without `status`, which the pantry owns."""

    name: str = Field(min_length=1)
    category: PantryCategory
    aliases: tuple[str, ...] = ()
    typical_interval_days: int | None = Field(default=None, gt=0)
    last_purchased: date | None = Field(
        default=None, description="A real purchase date; blank if it was already in the house"
    )
    default_qty: float | None = Field(default=None, gt=0)
    default_unit: str | None = None
    meijer_product_id: str | None = None
    meijer_url: MeijerUrl | None = None
    preferred_product_name: str | None = None
    substitute_ok: bool = True
    for_miles: bool = False
    notes: str | None = None


class SeedResult(Contract):
    inserted: tuple[str, ...] = Field(description="New items, in seed order")
    updated: tuple[str, ...] = Field(description="Existing items, in seed order")
    untouched: tuple[str, ...] = Field(description="Items not in the seed, left as they were")


def _iso(day: date | None) -> str | None:
    """Dates go to SQLite as ISO text: sqlite3's default date adapter is deprecated."""
    return day.isoformat() if day is not None else None


def _to_item(row: sqlite3.Row) -> PantryItem:
    """Map a row to the contract. Validation re-checks every field, including the meijer.com-only
    URL rule, so a bad row fails closed instead of reaching the cart."""
    fields = {key: row[key] for key in row.keys() if key != "updated_at"}
    try:
        fields["aliases"] = tuple(json.loads(row["aliases"] or "[]"))
        return PantryItem.model_validate(fields)
    except (ValueError, TypeError):
        logger.error(
            "pantry_item %s (%r) is invalid; re-run the seed loader to fix it",
            row["id"],
            row["name"],
        )
        raise


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
        name_key(item.name),
    )


def _is_due(item: PantryItem, on: date) -> bool:
    if item.status == "buy_next_time":
        return True
    asked_from = _ask_date(item)
    return asked_from is not None and on >= asked_from


def _median_gap(dates: Sequence[date]) -> int | None:
    """PLAN: after two purchases the interval is the median gap between them. Takes distinct dates
    in order; rounds half up (10.5 → 11). None with fewer than two dates."""
    if len(dates) < 2:
        return None
    gaps = [(later - earlier).days for earlier, later in pairwise(dates)]
    return math.floor(statistics.median(gaps) + 0.5)


# Seed fields the pantry learns or owns once an item exists; everything else is the product map.
_LEARNED_SEED_FIELDS = frozenset({"name", "typical_interval_days", "last_purchased"})


def _product_map(seed: SeedItem) -> dict[str, Any]:
    """The columns a seed row owns on an existing item: the product map, not what's been learned."""
    columns = seed.model_dump(exclude=set(_LEARNED_SEED_FIELDS))
    return {**columns, "aliases": json.dumps(list(seed.aliases))}


def _namespace_clashes(seeds: Sequence[SeedItem], untouched: Iterable[PantryItem]) -> list[str]:
    """Names and aliases (casefolded) claimed by two different items once the seed is applied."""
    claims = [(f"seed row {seed.name!r}", seed.name, seed.aliases) for seed in seeds]
    claims += [(f"existing item {item.name!r}", item.name, item.aliases) for item in untouched]
    owners: dict[str, str] = {}
    clashes = []
    for owner, name, aliases in claims:
        for key in sorted({name_key(name), *(name_key(alias) for alias in aliases)}):
            if key in owners:
                clashes.append(f"{key!r} is claimed by both {owners[key]} and {owner}")
            else:
                owners[key] = owner
    return clashes


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
        wanted = name_key(name)
        items = self.list_items()
        by_name = next((item for item in items if name_key(item.name) == wanted), None)
        if by_name is not None:
            return by_name
        return next(
            (item for item in items if wanted in {name_key(alias) for alias in item.aliases}),
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
        with self._write():
            item = self.get_item(name)
            if item is None:
                return None
            self._conn.execute(
                "UPDATE pantry_item SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, item.id),
            )
        logger.info("pantry: %s is now %s", item.name, status)
        return self._reread(item.id)

    def log_purchase(
        self, name: str, on: date, qty: float | None = None, price_cents: int | None = None
    ) -> PantryItem | None:
        """Record a purchase of the item called `name` (or carrying it as an alias) on `on`.

        The latest purchase restocks the item (`status` 'have', `last_purchased` = `on`). A
        back-dated one only adds history. A staple with two or more distinct purchase dates learns
        its interval: the median gap. A purchase already logged for that item and day writes
        nothing, so replays are safe. Returns the updated item, or None if the name is unknown.
        Raises ValueError for a quantity that isn't positive and finite, or a negative price.
        """
        if qty is not None:
            require_positive(qty, "qty")
        if price_cents is not None and price_cents < 0:
            raise ValueError(f"price_cents can't be negative, got {price_cents!r}")
        with self._write():
            item = self.get_item(name)
            if item is None:
                return None
            already = self._conn.execute(
                "SELECT 1 FROM purchase_log WHERE item_id = ? AND purchased_on = ?",
                (item.id, on.isoformat()),
            ).fetchone()
            if already:
                logger.info(
                    "pantry: %s purchase on %s already logged; nothing to do", item.name, on
                )
                return item
            self._conn.execute(
                "INSERT INTO purchase_log (item_id, purchased_on, qty, price_cents) "
                "VALUES (?, ?, ?, ?)",
                (item.id, on.isoformat(), qty, price_cents),
            )
            latest = item.last_purchased is None or on >= item.last_purchased
            learned = (
                _median_gap(self._purchase_dates(item.id)) if item.category == "staple" else None
            )
            self._conn.execute(
                "UPDATE pantry_item SET status = ?, last_purchased = ?, typical_interval_days = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    "have" if latest else item.status,
                    on.isoformat() if latest else _iso(item.last_purchased),
                    learned if learned is not None else item.typical_interval_days,
                    item.id,
                ),
            )
        logger.info(
            "pantry: logged %s purchase on %s (%s)",
            item.name,
            on,
            "restocked" if latest else "history only",
        )
        return self._reread(item.id)

    def load_seed(self, items: Iterable[SeedItem]) -> SeedResult:
        """Apply the seed CSV (the ingredient → Meijer product map), all or nothing.

        A seed row matches an existing item by name only (never by alias), ignoring case. A new
        name is inserted, with a `seed` purchase-log row when the seed has a real purchase date.
        An existing item gets its product map rewritten from the seed, but never its status, last
        purchase or history. Its interval follows the seed only until it has two distinct purchase
        dates (then learning owns it). Raises ValueError, writing nothing, when two items would
        claim the same name or alias.
        """
        seeds = tuple(items)
        with self._write():
            existing = {name_key(item.name): item for item in self.list_items()}
            seed_keys = {name_key(seed.name) for seed in seeds}
            untouched = [item for key, item in existing.items() if key not in seed_keys]
            clashes = _namespace_clashes(seeds, untouched)
            if clashes:
                raise ValueError(
                    "the seed would give two items the same name or alias: " + "; ".join(clashes)
                )
            inserted, updated = [], []
            for seed in seeds:
                match = existing.get(name_key(seed.name))
                if match is None:
                    self._insert_seed(seed)
                    inserted.append(seed.name)
                else:
                    self._update_from_seed(match, seed)
                    updated.append(match.name)
        logger.info(
            "pantry: seed loaded, %d inserted, %d updated, %d untouched",
            len(inserted),
            len(updated),
            len(untouched),
        )
        return SeedResult(
            inserted=tuple(inserted),
            updated=tuple(updated),
            untouched=tuple(sorted((item.name for item in untouched), key=name_key)),
        )

    def _insert_seed(self, seed: SeedItem) -> None:
        columns = {
            "name": seed.name,
            "typical_interval_days": seed.typical_interval_days,
            "last_purchased": _iso(seed.last_purchased),
            **_product_map(seed),
        }
        cursor = self._conn.execute(
            f"INSERT INTO pantry_item ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            tuple(columns.values()),
        )
        if seed.last_purchased is not None:
            self._conn.execute(
                "INSERT INTO purchase_log (item_id, purchased_on, source) VALUES (?, ?, 'seed')",
                (cursor.lastrowid, seed.last_purchased.isoformat()),
            )

    def _update_from_seed(self, item: PantryItem, seed: SeedItem) -> None:
        interval = item.typical_interval_days
        learning_owns_it = _median_gap(self._purchase_dates(item.id)) is not None
        if seed.typical_interval_days is not None and not learning_owns_it:
            interval = seed.typical_interval_days
        columns = {**_product_map(seed), "typical_interval_days": interval}
        assignments = ", ".join(f"{column} = ?" for column in columns)
        self._conn.execute(
            f"UPDATE pantry_item SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*columns.values(), item.id),
        )

    def _purchase_dates(self, item_id: int) -> tuple[date, ...]:
        rows = self._conn.execute(
            "SELECT DISTINCT purchased_on FROM purchase_log WHERE item_id = ? ORDER BY purchased_on",
            (item_id,),
        ).fetchall()
        return tuple(date.fromisoformat(row[0]) for row in rows)

    def _reread(self, item_id: int) -> PantryItem:
        row = self._conn.execute("SELECT * FROM pantry_item WHERE id = ?", (item_id,)).fetchone()
        return _to_item(row)

    @contextmanager
    def _write(self) -> Iterator[None]:
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
            yield
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:  # a failed COMMIT can leave it open
                self._conn.rollback()
            raise
