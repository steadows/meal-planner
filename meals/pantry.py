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
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, NamedTuple

from pydantic import Field

from meals.contracts import Contract, MeijerUrl, PantryCategory, PantryItem, PantryStatus
from meals.rollup import name_key, require_positive

logger = logging.getLogger(__name__)

# PLAN: ask about a staple once 90% of its interval has passed. Kept as a ratio of integers so
# the due date is exact (0.9 isn't representable in binary floating point).
ASK_AT_NUMERATOR, ASK_AT_DENOMINATOR = 9, 10
# PLAN: after two purchases, the interval is learned from the gap between them (staples only).
LEARN_AFTER_PURCHASES = 2
# A sanity bound for a first-guess interval: ten years. Bigger values overflow date arithmetic.
MAX_INTERVAL_DAYS = 3650


class SeedItem(Contract):
    """One row of the seed CSV (the ingredient → Meijer product map): a `PantryItem` without the
    database's `id` and without `status`, which the pantry owns."""

    name: str = Field(min_length=1)
    category: PantryCategory
    aliases: tuple[str, ...] = ()
    typical_interval_days: int | None = Field(default=None, gt=0, le=MAX_INTERVAL_DAYS)
    last_purchased: date | None = Field(
        default=None, description="A real purchase date; blank if it was already in the house"
    )
    default_qty: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    default_unit: str | None = None
    meijer_product_id: str | None = None
    meijer_url: MeijerUrl | None = None
    preferred_product_name: str | None = None
    substitute_ok: bool = True
    for_miles: bool = False
    notes: str | None = None


class _Existing(NamedTuple):
    """What load_seed needs from a stored item, read without validating the whole row, so a row
    that fails validation (say, a hand-edited URL) can still be repaired by re-running the seed."""

    id: int
    name: str
    aliases: tuple[str, ...]
    typical_interval_days: int | None


class SeedResult(Contract):
    inserted: tuple[str, ...] = Field(description="New items, in seed order")
    updated: tuple[str, ...] = Field(description="Existing items, in seed order")
    untouched: tuple[str, ...] = Field(description="Items not in the seed, left as they were")


def _as_day(on: date) -> date:
    """A datetime is also a date; use its calendar day, as the schema stores DATE."""
    return on.date() if isinstance(on, datetime) else on


def _iso(day: date | None) -> str | None:
    """Dates go to SQLite as ISO text: sqlite3's default date adapter is deprecated."""
    return day.isoformat() if day is not None else None


class PantryRowError(ValueError):
    """A stored pantry row fails validation (say, a hand-edited URL that isn't meijer.com). Reads
    fail closed; the message names the row and the fix, because it's what reaches Steve."""


def _stored_aliases(row: sqlite3.Row) -> tuple[str, ...]:
    """A row's aliases for load_seed's name check, tolerating a corrupt JSON cell (logged): the
    seed rewrites aliases anyway, so this must not block the repair."""
    try:
        return tuple(json.loads(row["aliases"] or "[]"))
    except (ValueError, TypeError):
        logger.warning("pantry_item %s (%r) has unreadable aliases", row["id"], row["name"])
        return ()


def _to_item(row: sqlite3.Row) -> PantryItem:
    """Map a row to the contract. Validation re-checks every field, including the meijer.com-only
    URL rule, so a bad row fails closed instead of reaching the cart."""
    fields = {key: row[key] for key in row.keys() if key != "updated_at"}
    try:
        fields["aliases"] = tuple(json.loads(row["aliases"] or "[]"))
        return PantryItem.model_validate(fields)
    except (ValueError, TypeError) as error:
        message = (
            f"pantry_item {row['id']} ({row['name']!r}) is invalid; "
            f"re-run the seed loader with a corrected row to fix it. Details: {error}"
        )
        logger.error(message)
        raise PantryRowError(message) from error


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
    if len(dates) < LEARN_AFTER_PURCHASES:
        return None
    gaps = [(later - earlier).days for earlier, later in pairwise(dates)]
    return math.floor(statistics.median(gaps) + 0.5)


# Seed fields the pantry learns or owns once an item exists; everything else is the product map.
_LEARNED_SEED_FIELDS = frozenset({"name", "typical_interval_days", "last_purchased"})


def _product_map(seed: SeedItem) -> dict[str, Any]:
    """The columns a seed row owns on an existing item: the product map, not what's been learned."""
    columns = seed.model_dump(exclude=set(_LEARNED_SEED_FIELDS))
    return {**columns, "aliases": json.dumps(list(seed.aliases))}


def _namespace_clashes(seeds: Sequence[SeedItem], untouched: Iterable[_Existing]) -> list[str]:
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
        on = _as_day(on)
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
            updated = self._reread(item.id)  # before COMMIT, so no other writer's change leaks in
        logger.info("pantry: %s is now %s", item.name, status)
        return updated

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
        if price_cents is not None and (
            isinstance(price_cents, bool) or not isinstance(price_cents, int) or price_cents < 0
        ):
            raise ValueError(
                f"price_cents must be a whole, non-negative number, got {price_cents!r}"
            )
        on = _as_day(on)
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
            updated = self._reread(item.id)  # before COMMIT, so no other writer's change leaks in
        logger.info(
            "pantry: logged %s purchase on %s (%s)",
            item.name,
            on,
            "restocked" if latest else "history only",
        )
        return updated

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
            existing = {name_key(row.name): row for row in self._existing_rows()}
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

    def _update_from_seed(self, item: _Existing, seed: SeedItem) -> None:
        interval = item.typical_interval_days
        learning_owns_it = (
            seed.category == "staple"
            and len(self._purchase_dates(item.id)) >= LEARN_AFTER_PURCHASES
        )
        if seed.typical_interval_days is not None and not learning_owns_it:
            interval = seed.typical_interval_days
        columns = {**_product_map(seed), "typical_interval_days": interval}
        assignments = ", ".join(f"{column} = ?" for column in columns)
        self._conn.execute(
            f"UPDATE pantry_item SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*columns.values(), item.id),
        )

    def _existing_rows(self) -> tuple[_Existing, ...]:
        rows = self._conn.execute(
            "SELECT id, name, aliases, typical_interval_days FROM pantry_item ORDER BY id"
        ).fetchall()
        return tuple(
            _Existing(row["id"], row["name"], _stored_aliases(row), row["typical_interval_days"])
            for row in rows
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
