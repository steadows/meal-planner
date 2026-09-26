"""SqlitePantry writes: log_purchase, load_seed and the write-transaction discipline (task B2).

Authority: the B2 dispatch brief (its numbered requirements are cited as [R1]..[R17]),
`.context/seams/lane-b-pantry.md` (Revision 1 wins over the older text below it) and PLAN.md,
"Learning when staples run out". Expected values are worked out by hand, never from the code.
Setup writes rows straight to the tables (`insert_item`, `_log`), not through the code under test.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from meals.contracts import PantryItem
from meals.pantry import PantryRowError, SeedItem, SeedResult, SqlitePantry

Insert = Callable[[PantryItem], None]
Rows = list[tuple[Any, ...]]

ON = date(2026, 9, 26)
D0 = date(2026, 1, 5)
OLD = "2000-01-01 00:00:00"  # an updated_at that no real write can leave behind
URL_A = "https://www.meijer.com/shopping/product/example-olive-oil/100001.html"
URL_B = "https://www.meijer.com/shopping/product/example-olive-oil-1l/200002.html"
URL_C = "https://www.meijer.com/shopping/product/example-tortillas/100009.html"


def _day(offset: int) -> date:
    return D0 + timedelta(days=offset)


def _item(id_: int, name: str, category: str = "staple", **fields: object) -> PantryItem:
    return PantryItem.model_validate({"id": id_, "name": name, "category": category, **fields})


def _seed(name: str, category: str = "staple", **fields: object) -> SeedItem:
    return SeedItem.model_validate({"name": name, "category": category, **fields})


def _log(db: sqlite3.Connection, item_id: int, on: date, source: str = "meijer_pickup") -> None:
    db.execute(
        "INSERT INTO purchase_log (item_id, purchased_on, source) VALUES (?, ?, ?)",
        (item_id, on.isoformat(), source),
    )
    db.commit()


def _age(db: sqlite3.Connection) -> None:
    db.execute("UPDATE pantry_item SET updated_at = ?", (OLD,))
    db.commit()


def _updated_at(db: sqlite3.Connection, item_id: int) -> Any:
    return db.execute("SELECT updated_at FROM pantry_item WHERE id = ?", (item_id,)).fetchone()[0]


def _interval(db: sqlite3.Connection, item_id: int) -> Any:
    sql = "SELECT typical_interval_days FROM pantry_item WHERE id = ?"
    return db.execute(sql, (item_id,)).fetchone()[0]


def _log_rows(db: sqlite3.Connection) -> Rows:
    sql = "SELECT item_id, purchased_on, qty, price_cents, source FROM purchase_log ORDER BY id"
    return [tuple(row) for row in db.execute(sql)]


def _snapshot(db: sqlite3.Connection) -> tuple[Rows, Rows]:
    items = [tuple(row) for row in db.execute("SELECT * FROM pantry_item ORDER BY id")]
    return items, [tuple(row) for row in db.execute("SELECT * FROM purchase_log ORDER BY id")]


def _path(db: sqlite3.Connection) -> Path:
    return Path(db.execute("PRAGMA database_list").fetchone()[2])


def _by_id(db: sqlite3.Connection) -> tuple[PantryItem, ...]:
    return tuple(sorted(SqlitePantry(db).list_items(), key=lambda item: item.id))


# ── log_purchase: arguments and the first purchase ───────────────────────────


def test_logging_an_unknown_item_returns_none_and_writes_nothing(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R1]
    insert_item(_item(1, "rice", typical_interval_days=56))
    before = _snapshot(db)
    assert SqlitePantry(db).log_purchase("saffron", ON, qty=1, price_cents=399) is None
    assert _snapshot(db) == before


@pytest.mark.parametrize(
    ("qty", "price_cents"),
    [
        (0, None),
        (-1.5, None),
        (math.nan, None),
        (math.inf, None),
        (-math.inf, None),
        (None, -1),
        # review finding 4: a price is a whole number of cents, and a bool isn't one
        (None, 4.99),
        (None, True),
        (None, False),
    ],
    ids=[
        "zero qty",
        "negative qty",
        "nan qty",
        "inf qty",
        "-inf qty",
        "negative price",
        "fractional price",
        "price True",
        "price False",
    ],
)
def test_a_bad_quantity_or_price_raises_and_writes_nothing(
    db: sqlite3.Connection, insert_item: Insert, qty: float | None, price_cents: int | None
) -> None:  # [R2]
    insert_item(_item(1, "rice"))
    before = _snapshot(db)
    with pytest.raises(ValueError):
        SqlitePantry(db).log_purchase("rice", ON, qty=qty, price_cents=price_cents)
    assert _snapshot(db) == before


@pytest.mark.parametrize(
    ("qty", "price_cents"), [(None, None), (1.5, 0)], ids=["no qty or price", "free, 1.5"]
)
def test_a_new_purchase_logs_one_row_and_returns_the_restocked_item(
    db: sqlite3.Connection, insert_item: Insert, qty: float | None, price_cents: int | None
) -> None:  # [R3] [R4] [R6: one date keeps the interval] [R8] [R16]
    insert_item(_item(1, "butter", status="buy_next_time", typical_interval_days=21))
    _age(db)
    logged = SqlitePantry(db).log_purchase("butter", ON, qty=qty, price_cents=price_cents)
    expected = _item(1, "butter", typical_interval_days=21, last_purchased=ON)
    assert logged == expected
    assert SqlitePantry(db).get_item("butter") == expected
    assert _log_rows(db) == [(1, "2026-09-26", qty, price_cents, "meijer_pickup")]
    assert _updated_at(db, 1) != OLD


@pytest.mark.parametrize("name", ["EVOO", "  Olive Oil "])
def test_log_purchase_finds_the_item_by_name_or_alias_ignoring_case_and_spaces(
    db: sqlite3.Connection, insert_item: Insert, name: str
) -> None:  # seam map, SqlitePantry "Match"
    insert_item(_item(1, "olive oil", aliases=("evoo",)))
    logged = SqlitePantry(db).log_purchase(name, ON)
    assert logged is not None
    assert logged.name == "olive oil"
    assert [row[:2] for row in _log_rows(db)] == [(1, "2026-09-26")]


def test_a_purchase_logged_with_a_datetime_is_stored_as_its_date(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # review finding 3
    insert_item(_item(1, "rice", status="buy_next_time"))
    logged = SqlitePantry(db).log_purchase("rice", datetime(2026, 9, 26, 10, 0))
    assert logged == _item(1, "rice", last_purchased=ON)
    stored = db.execute("SELECT last_purchased FROM pantry_item WHERE id = 1").fetchone()[0]
    assert stored == "2026-09-26"
    # A later time on the same day is the same (item, date), so it's a replay.
    SqlitePantry(db).log_purchase("rice", datetime(2026, 9, 26, 18, 30))
    assert [row[:2] for row in _log_rows(db)] == [(1, "2026-09-26")]


def test_staples_due_on_a_datetime_matches_its_date(
    db: sqlite3.Connection, insert_item: Insert, sample_pantry_items: tuple[PantryItem, ...]
) -> None:  # review finding 3
    for item in sample_pantry_items:
        insert_item(item)
    pantry = SqlitePantry(db)
    due = pantry.staples_due(ON)
    assert due
    assert pantry.staples_due(datetime(2026, 9, 26, 10, 0)) == due


# ── log_purchase: current stock vs history ───────────────────────────────────


@pytest.mark.parametrize(
    "last_purchased", [ON - timedelta(days=30), ON], ids=["bought before", "same day, not logged"]
)
def test_a_purchase_on_or_after_the_last_one_restocks_the_item(
    db: sqlite3.Connection, insert_item: Insert, last_purchased: date
) -> None:  # [R4] (never-bought is covered above)
    insert_item(_item(1, "tahini", status="buy_next_time", last_purchased=last_purchased))
    logged = SqlitePantry(db).log_purchase("tahini", ON)
    assert logged == _item(1, "tahini", last_purchased=ON)
    stored = db.execute("SELECT status, last_purchased FROM pantry_item WHERE id = 1").fetchone()
    assert tuple(stored) == ("have", "2026-09-26")
    assert len(_log_rows(db)) == 1


@pytest.mark.parametrize(
    ("last_is_logged", "interval"),
    [(True, 12), (False, 30)],
    ids=["log holds the last date: learns a 12-day gap", "log is empty: one date, no learning"],
)
def test_a_back_dated_purchase_is_logged_but_never_changes_current_stock(
    db: sqlite3.Connection, insert_item: Insert, last_is_logged: bool, interval: int
) -> None:  # [R5] [R6: back-dated inserts learn too] [R8]
    insert_item(
        _item(1, "butter", status="buy_next_time", typical_interval_days=30, last_purchased=ON)
    )
    if last_is_logged:
        _log(db, 1, ON)
    _age(db)
    logged = SqlitePantry(db).log_purchase("butter", ON - timedelta(days=12), qty=1)
    expected = _item(
        1, "butter", status="buy_next_time", typical_interval_days=interval, last_purchased=ON
    )
    assert logged == expected
    assert SqlitePantry(db).get_item("butter") == expected
    rows = _log_rows(db)
    assert (1, "2026-09-14", 1.0, None, "meijer_pickup") in rows
    assert len(rows) == (2 if last_is_logged else 1)
    assert _updated_at(db, 1) != OLD


# ── log_purchase: learned intervals (PLAN.md, Learning when staples run out) ─


@pytest.mark.parametrize(
    ("logged", "new", "interval"),
    [
        # gaps 10, 11: median 10.5 rounds half up to 11 (Python's round() gives 10)
        ((0, 10), 21, 11),
        # gaps 60, 60, 60, 10: the median, not the mean (47.5) and not the latest gap
        ((0, 60, 120, 180), 190, 60),
        # distinct dates 0 and 10 give one gap; counting both day-0 rows gives median(0, 10) = 5
        ((0, 0), 10, 10),
        # logged out of order plus a back-dated purchase: dates 0, 12, 30, gaps 12 and 18
        ((30, 0), 12, 15),
    ],
    ids=["half up", "median", "distinct dates", "sorted by date"],
)
def test_a_staples_interval_becomes_the_median_gap_between_distinct_purchase_dates(
    db: sqlite3.Connection,
    insert_item: Insert,
    logged: tuple[int, ...],
    new: int,
    interval: int,
) -> None:  # [R6]
    insert_item(_item(1, "olive oil", typical_interval_days=70, last_purchased=_day(max(logged))))
    for offset in logged:
        _log(db, 1, _day(offset))
    item = SqlitePantry(db).log_purchase("olive oil", _day(new))
    assert item is not None
    assert item.typical_interval_days == interval
    assert _interval(db, 1) == interval


@pytest.mark.parametrize(("category", "interval"), [("perishable", None), ("fallback", 30)])
def test_learning_never_changes_a_perishable_or_fallback_interval(
    db: sqlite3.Connection, insert_item: Insert, category: str, interval: int | None
) -> None:  # [R6]
    insert_item(_item(1, "eggs", category, typical_interval_days=interval, last_purchased=D0))
    _log(db, 1, D0)
    item = SqlitePantry(db).log_purchase("eggs", _day(7))
    assert item is not None
    assert (item.typical_interval_days, item.last_purchased) == (interval, _day(7))
    assert _interval(db, 1) == interval
    assert len(_log_rows(db)) == 2


# ── log_purchase: replays and other writers ──────────────────────────────────


@pytest.mark.parametrize("first", ["logged", "seeded"])
def test_logging_the_same_item_on_the_same_date_again_writes_nothing(
    db: sqlite3.Connection, insert_item: Insert, first: str
) -> None:  # [R7]
    insert_item(_item(1, "butter", typical_interval_days=21))
    pantry = SqlitePantry(db)
    if first == "logged":
        assert pantry.log_purchase("butter", ON, qty=1, price_cents=499) is not None
    else:
        _log(db, 1, ON, source="seed")
    db.execute(
        "UPDATE pantry_item SET status = 'buy_next_time', last_purchased = ?, updated_at = ?",
        (ON.isoformat(), OLD),
    )
    db.commit()
    before = _snapshot(db)
    again = pantry.log_purchase("butter", ON, qty=2, price_cents=999)
    assert _snapshot(db) == before
    assert again == _item(
        1, "butter", status="buy_next_time", typical_interval_days=21, last_purchased=ON
    )


def test_a_purchase_another_connection_logged_counts_toward_the_next_one(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R9]
    insert_item(_item(1, "olive oil", typical_interval_days=70))
    pantry = SqlitePantry(db)
    assert pantry.log_purchase("olive oil", D0) is not None
    # Another process logs a later purchase, then flags the item.
    later = _day(21).isoformat()
    with closing(sqlite3.connect(_path(db))) as other, other:
        other.execute("INSERT INTO purchase_log (item_id, purchased_on) VALUES (1, ?)", (later,))
        other.execute(
            "UPDATE pantry_item SET last_purchased = ?, status = 'buy_next_time' WHERE id = 1",
            (later,),
        )
    item = pantry.log_purchase("olive oil", _day(10))
    # Dates 0, 10, 21: gaps 10 and 11 give 11 (without the other row, 10). Day 10 is older than
    # the other process's day 21, so it is history: the flag and the last date stay.
    assert item == _item(
        1, "olive oil", status="buy_next_time", typical_interval_days=11, last_purchased=_day(21)
    )
    assert len(_log_rows(db)) == 3


def test_a_purchase_reads_and_writes_only_its_own_items_rows(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R3] [R4] [R6] [R7]: every lookup and write is keyed by this item's id
    insert_item(
        _item(1, "rice", status="buy_next_time", typical_interval_days=56, last_purchased=ON)
    )
    insert_item(
        _item(2, "butter", typical_interval_days=21, last_purchased=ON - timedelta(days=20))
    )
    _log(db, 1, ON - timedelta(days=30))
    _log(db, 1, ON)
    _log(db, 2, ON - timedelta(days=20))
    _age(db)
    rice_row = tuple(db.execute("SELECT * FROM pantry_item WHERE id = 1").fetchone())
    logged = SqlitePantry(db).log_purchase("butter", ON)
    # Butter's own dates give one 20-day gap (rice's would make it 15), and rice already having a
    # row on ON doesn't make butter's purchase a replay.
    assert logged == _item(2, "butter", typical_interval_days=20, last_purchased=ON)
    assert tuple(db.execute("SELECT * FROM pantry_item WHERE id = 1").fetchone()) == rice_row
    rows = _log_rows(db)
    assert len(rows) == 4
    assert rows[-1][:2] == (2, "2026-09-26")


# ── transaction discipline (seam map, Revision 1: "Transactions") ─────────────


@pytest.mark.parametrize("write", ["log_purchase", "load_seed"])
def test_a_write_refuses_to_run_inside_the_callers_open_transaction(
    db: sqlite3.Connection, insert_item: Insert, write: str
) -> None:  # [R10]
    insert_item(_item(1, "rice", notes="committed"))
    db.execute("UPDATE pantry_item SET notes = 'pending' WHERE id = 1")
    assert db.in_transaction
    pantry = SqlitePantry(db)
    with pytest.raises(sqlite3.ProgrammingError):
        if write == "log_purchase":
            pantry.log_purchase("rice", ON)
        else:
            pantry.load_seed([_seed("couscous", last_purchased=ON)])
    # Still the caller's transaction: open, holding its own write, and not committed by us.
    assert db.in_transaction
    assert db.execute("SELECT notes FROM pantry_item").fetchone()[0] == "pending"
    with closing(sqlite3.connect(_path(db))) as other:
        assert other.execute("SELECT name, notes FROM pantry_item").fetchall() == [
            ("rice", "committed")
        ]
        assert other.execute("SELECT COUNT(*) FROM purchase_log").fetchone() == (0,)
    db.rollback()
    assert db.execute("SELECT notes FROM pantry_item").fetchone()[0] == "committed"


@pytest.mark.parametrize("write", ["log_purchase", "load_seed"])
def test_a_write_takes_the_write_lock_before_it_reads_anything(
    db: sqlite3.Connection, insert_item: Insert, write: str
) -> None:  # [R9]; seam map: every read-modify-write runs in one BEGIN IMMEDIATE transaction
    insert_item(_item(1, "rice"))
    seen: list[str] = []
    db.set_trace_callback(seen.append)
    try:
        if write == "log_purchase":
            SqlitePantry(db).log_purchase("rice", ON)
        else:
            SqlitePantry(db).load_seed([_seed("rice"), _seed("couscous")])
    finally:
        db.set_trace_callback(None)
    begins = [sql for sql in seen if sql.lstrip().upper().startswith("BEGIN")]
    assert len(begins) == 1, seen
    assert seen[0] == begins[0], seen
    assert re.match(r"(?i)begin\s+immediate", seen[0].lstrip()), seen


@pytest.mark.parametrize(
    "trigger",
    [
        "CREATE TRIGGER boom AFTER INSERT ON purchase_log BEGIN SELECT RAISE(ABORT, 'boom'); END",
        "CREATE TRIGGER boom BEFORE UPDATE ON pantry_item BEGIN SELECT RAISE(ABORT, 'boom'); END",
    ],
    ids=["log insert fails", "item update fails"],
)
def test_a_purchase_that_fails_halfway_leaves_nothing_behind(
    db: sqlite3.Connection, insert_item: Insert, trigger: str
) -> None:  # seam map: one BEGIN IMMEDIATE transaction per write; rolls back only its own
    insert_item(_item(1, "rice", status="buy_next_time", typical_interval_days=56))
    db.execute(trigger)
    db.commit()
    before = _snapshot(db)
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        SqlitePantry(db).log_purchase("rice", ON)
    assert not db.in_transaction
    assert _snapshot(db) == before


# ── load_seed: inserts ───────────────────────────────────────────────────────


def test_load_seed_inserts_new_items_with_every_field_and_logs_only_real_purchase_dates(
    db: sqlite3.Connection,
) -> None:  # [R12] [R16]
    full = _seed(
        "olive oil",
        aliases=("evoo", "extra virgin olive oil"),
        typical_interval_days=70,
        last_purchased=D0,
        default_qty=1,
        default_unit="bottle",
        meijer_product_id="100001",
        meijer_url=URL_A,
        preferred_product_name="Example Olive Oil 16.9 oz",
        substitute_ok=False,
        for_miles=True,
        notes="the green tin",
    )
    bare = _seed("couscous", typical_interval_days=60)
    result = SqlitePantry(db).load_seed([bare, full])
    assert result == SeedResult(inserted=("couscous", "olive oil"), updated=(), untouched=())
    items = {item.name: item for item in SqlitePantry(db).list_items()}
    assert items.keys() == {"olive oil", "couscous"}
    for seed in (full, bare):
        # Only the seed's own fields, so a new PantryItem field doesn't break this test.
        assert items[seed.name].model_dump(include=set(SeedItem.model_fields)) == seed.model_dump()
        assert items[seed.name].status == "have"
    # One seed purchase row, on olive oil (inserted second, so not id 1), for its genuine date.
    # Couscous gets none: dates are never invented.
    joined = db.execute(
        "SELECT i.name, l.purchased_on, l.source FROM purchase_log l "
        "JOIN pantry_item i ON i.id = l.item_id"
    )
    assert [tuple(row) for row in joined] == [("olive oil", "2026-01-05", "seed")]


# ── load_seed: updates ───────────────────────────────────────────────────────


def test_load_seed_repairs_a_stored_url_that_fails_the_meijer_check(
    db: sqlite3.Connection,
) -> None:  # review finding 1: re-running the seed is how a bad product link gets fixed
    db.execute(
        "INSERT INTO pantry_item (name, category, meijer_url) VALUES (?, ?, ?)",
        ("rice", "staple", "https://evil.example/rice"),
    )
    db.commit()
    pantry = SqlitePantry(db)
    with pytest.raises(PantryRowError):
        pantry.list_items()  # reads still fail closed
    fixed = "https://www.meijer.com/shopping/product/example-rice/100002.html"
    result = pantry.load_seed([_seed("rice", meijer_url=fixed)])
    assert result == SeedResult(inserted=(), updated=("rice",), untouched=())
    assert pantry.list_items() == (_item(1, "rice", meijer_url=fixed),)


def test_load_seed_runs_while_an_untouched_row_fails_the_meijer_check(
    db: sqlite3.Connection,
) -> None:  # review finding 1: load_seed must not need to validate every row to run
    db.execute(
        "INSERT INTO pantry_item (name, category, meijer_url) VALUES (?, ?, ?)",
        ("tahini", "staple", "https://evil.example/tahini"),
    )
    db.commit()
    result = SqlitePantry(db).load_seed([_seed("rice")])
    assert result == SeedResult(inserted=("rice",), updated=(), untouched=("tahini",))
    names = [row[0] for row in db.execute("SELECT name FROM pantry_item ORDER BY id")]
    assert names == ["tahini", "rice"]


def test_load_seed_rewrites_the_product_map_but_never_stock_or_history(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R13] [R8] [R16]
    insert_item(
        _item(
            1,
            "olive oil",
            aliases=("evoo",),
            status="buy_next_time",
            typical_interval_days=70,
            last_purchased=D0,
            default_qty=1,
            default_unit="bottle",
            meijer_product_id="100001",
            meijer_url=URL_A,
            preferred_product_name="Example Olive Oil 16.9 oz",
            notes="the green tin",
        )
    )
    insert_item(
        _item(
            2,
            "tortillas",
            "perishable",
            aliases=("flour tortillas",),
            status="buy_next_time",
            last_purchased=_day(-3),
            default_qty=1,
            default_unit="pack",
            meijer_product_id="100009",
            meijer_url=URL_C,
            preferred_product_name="Example Flour Tortillas",
            substitute_ok=False,
            for_miles=True,
            notes="soft taco size",
        )
    )
    _log(db, 1, D0)
    _log(db, 2, _day(-3))
    _age(db)
    log_before = _log_rows(db)
    result = SqlitePantry(db).load_seed(
        [
            _seed(
                "olive oil",
                aliases=("extra virgin olive oil",),
                typical_interval_days=63,
                last_purchased=_day(5),
                default_qty=2,
                default_unit="l",
                meijer_product_id="200002",
                meijer_url=URL_B,
                preferred_product_name="Example Olive Oil 1 L",
                substitute_ok=False,
                for_miles=True,
            ),
            _seed("tortillas", "fallback"),  # every blank field clears; flags go to defaults
        ]
    )
    assert result == SeedResult(inserted=(), updated=("olive oil", "tortillas"), untouched=())
    assert _by_id(db) == (
        _item(
            1,
            "olive oil",
            aliases=("extra virgin olive oil",),
            status="buy_next_time",
            typical_interval_days=63,
            last_purchased=D0,
            default_qty=2,
            default_unit="l",
            meijer_product_id="200002",
            meijer_url=URL_B,
            preferred_product_name="Example Olive Oil 1 L",
            substitute_ok=False,
            for_miles=True,
        ),
        _item(2, "tortillas", "fallback", status="buy_next_time", last_purchased=_day(-3)),
    )
    assert _log_rows(db) == log_before
    assert OLD not in (_updated_at(db, 1), _updated_at(db, 2))


@pytest.mark.parametrize(
    ("logged", "seed_interval", "interval"),
    [
        ((), 63, 63),
        ((0,), 63, 63),
        ((0, 0), 63, 63),  # two rows, one distinct date: still the seed's guess
        ((0, 11), 63, 70),  # two distinct dates: learning owns the interval now
        ((0,), None, 70),  # a blank seed interval keeps the current one
    ],
    ids=["never bought", "one date", "one date twice", "two dates", "blank in the seed"],
)
def test_a_seed_interval_replaces_the_guess_until_learning_takes_over(
    db: sqlite3.Connection,
    insert_item: Insert,
    logged: tuple[int, ...],
    seed_interval: int | None,
    interval: int,
) -> None:  # [R13]
    last = _day(max(logged)) if logged else None
    insert_item(_item(1, "rice", typical_interval_days=70, last_purchased=last))
    for offset in logged:
        _log(db, 1, _day(offset))
    SqlitePantry(db).load_seed([_seed("rice", typical_interval_days=seed_interval)])
    assert _interval(db, 1) == interval


@pytest.mark.parametrize(
    ("category", "seed_category", "interval"),
    [
        ("fallback", "fallback", 21),
        ("perishable", "perishable", 21),
        ("staple", "fallback", 21),
        ("fallback", "staple", 14),  # baseline-green: now a staple, so learning owns it
    ],
    ids=[
        "fallback",
        "perishable",
        "staple re-seeded as a fallback",
        "fallback re-seeded as a staple",
    ],
)
def test_only_an_item_that_ends_up_a_staple_keeps_its_interval_after_two_purchases(
    db: sqlite3.Connection, insert_item: Insert, category: str, seed_category: str, interval: int
) -> None:  # review finding 2: only staples learn, and the category after the seed decides
    insert_item(_item(1, "nuggets", category, typical_interval_days=14, last_purchased=_day(14)))
    _log(db, 1, D0)
    _log(db, 1, _day(14))
    SqlitePantry(db).load_seed([_seed("nuggets", seed_category, typical_interval_days=21)])
    assert _interval(db, 1) == interval


def test_seed_rows_match_existing_names_ignoring_case_and_never_by_alias(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R12] [R13]
    insert_item(_item(1, "olive oil", aliases=("evoo",)))
    result = SqlitePantry(db).load_seed([_seed("Olive Oil"), _seed("EVOO")])
    assert result.inserted == ("EVOO",)
    assert [name.casefold() for name in result.updated] == ["olive oil"]
    first, second = _by_id(db)
    assert (first.id, first.name.casefold(), first.aliases) == (1, "olive oil", ())
    assert second.name == "EVOO"


def test_seed_names_match_existing_names_beyond_ascii_case(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R12] [R13]; seam map "Match": casefold(), not SQLite's ASCII-only NOCASE/lower()
    insert_item(_item(1, "jalapeño"))
    result = SqlitePantry(db).load_seed([_seed("JALAPEÑO")])
    assert result.inserted == ()
    assert [name.casefold() for name in result.updated] == ["jalapeño"]
    assert db.execute("SELECT COUNT(*) FROM pantry_item").fetchone()[0] == 1


# ── load_seed: the resulting namespace (seam map, Revision 1: "Seed upsert") ──

_TAHINI = _item(1, "tahini")
_OLIVE_OIL = _item(1, "olive oil", aliases=("evoo",))


@pytest.mark.parametrize(
    ("existing", "seed", "key"),
    [
        ([], [{"name": "couscous"}, {"name": "Rice"}, {"name": "rice"}], "rice"),
        (
            [_TAHINI],
            [{"name": "couscous"}, {"name": "sesame paste", "aliases": ["Tahini"]}],
            "tahini",
        ),
        (
            [_TAHINI],
            [{"name": "tahini"}, {"name": "sesame paste", "aliases": ["TAHINI"]}],
            "tahini",
        ),
        ([_OLIVE_OIL], [{"name": "couscous"}, {"name": "canola oil", "aliases": ["EVOO"]}], "evoo"),
        ([_OLIVE_OIL], [{"name": "couscous"}, {"name": "Evoo"}], "evoo"),
        (
            [],
            [
                {"name": "basmati", "aliases": ["long grain"]},
                {"name": "jasmine", "aliases": ["Long Grain"]},
            ],
            "long grain",
        ),
    ],
    ids=[
        "two seed rows share a name",
        "seed alias is an untouched item's name",
        "seed alias is another seed row's (existing) name",
        "seed alias is an untouched item's alias",
        "seed name is an untouched item's alias",
        "two seed rows share an alias",
    ],
)
def test_a_namespace_clash_raises_naming_the_key_and_writes_nothing(
    db: sqlite3.Connection,
    insert_item: Insert,
    existing: list[PantryItem],
    seed: list[dict[str, object]],
    key: str,
) -> None:  # [R14] [R11]
    for item in existing:
        insert_item(item)
    before = _snapshot(db)
    items = [SeedItem.model_validate({"category": "staple", **row}) for row in seed]
    with pytest.raises(ValueError, match=f"(?i){re.escape(key)}"):
        SqlitePantry(db).load_seed(items)
    assert _snapshot(db) == before


def test_an_alias_may_move_to_another_item_in_the_same_seed(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R14]: the RESULTING namespace is checked, not today's
    insert_item(_OLIVE_OIL)
    result = SqlitePantry(db).load_seed(
        [_seed("olive oil"), _seed("extra virgin olive oil", aliases=("evoo",))]
    )
    assert result == SeedResult(
        inserted=("extra virgin olive oil",), updated=("olive oil",), untouched=()
    )
    moved = SqlitePantry(db).get_item("evoo")
    assert moved is not None
    assert moved.name == "extra virgin olive oil"


def test_an_item_may_repeat_its_own_name_as_an_alias(db: sqlite3.Connection) -> None:  # [R14]
    result = SqlitePantry(db).load_seed([_seed("rice", aliases=("Rice", "RICE"))])
    assert result.inserted == ("rice",)
    assert db.execute("SELECT COUNT(*) FROM pantry_item").fetchone()[0] == 1


def test_a_seed_that_fails_halfway_leaves_nothing_behind(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R11]
    insert_item(_item(1, "rice", notes="before"))
    db.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON pantry_item WHEN NEW.name = 'boom' "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    db.commit()
    before = _snapshot(db)
    seed = [_seed("rice", notes="after"), _seed("couscous", last_purchased=D0), _seed("boom")]
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        SqlitePantry(db).load_seed(seed)
    assert not db.in_transaction
    assert _snapshot(db) == before


# ── load_seed: the result, and running it again ──────────────────────────────


def test_seed_result_keeps_csv_order_and_sorts_untouched_names(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [R12] [R13] [R15]
    for id_, name in enumerate(("tahini", "rice", "butter", "zaatar", "anise"), start=1):
        insert_item(_item(id_, name))
    names = ("tahini", "couscous", "butter", "rice", "allspice")
    result = SqlitePantry(db).load_seed([_seed(name) for name in names])
    assert result == SeedResult(
        inserted=("couscous", "allspice"),
        updated=("tahini", "butter", "rice"),
        untouched=("anise", "zaatar"),
    )


def test_running_the_same_seed_twice_updates_everything_and_logs_nothing_new(
    db: sqlite3.Connection,
) -> None:  # [R17]
    seed = [
        _seed("olive oil", typical_interval_days=70, last_purchased=D0, meijer_url=URL_A),
        _seed("couscous"),
    ]
    pantry = SqlitePantry(db)
    pantry.load_seed(seed)
    items, log = pantry.list_items(), _log_rows(db)
    assert len(items) == 2
    assert len(log) == 1
    again = pantry.load_seed(seed)
    assert again == SeedResult(inserted=(), updated=("olive oil", "couscous"), untouched=())
    assert pantry.list_items() == items
    assert _log_rows(db) == log


# ── SeedItem (seam map, SeedItem; the brief's interface) ─────────────────────


@pytest.mark.parametrize(
    "fields",
    [
        {"default_qty": 0},
        {"meijer_url": "https://evil.example/olive-oil"},
        {"status": "have"},
        {"id": 1},
        # review finding 6
        {"typical_interval_days": 3651},
        {"default_qty": math.inf},
        {"default_qty": math.nan},  # baseline-green: gt=0 already rejects nan
    ],
    ids=["qty 0", "non-meijer url", "status", "id", "interval over 3650", "qty inf", "qty nan"],
)
def test_a_seed_item_rejects_bad_or_extra_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SeedItem.model_validate({"name": "olive oil", "category": "staple", **fields})


def test_a_seed_item_allows_an_interval_of_exactly_3650_days() -> None:
    # review finding 6, the bound itself (baseline-green: there's no upper bound yet)
    item = SeedItem(name="rice", category="staple", typical_interval_days=3650)
    assert item.typical_interval_days == 3650


def test_a_seed_item_carries_every_pantry_item_field_but_the_pantrys_own() -> None:
    # review finding 5, a drift alarm (baseline-green). next_ask_on is contracts' pending field;
    # whether the seed sets it is PR 2's decision.
    owned_by_the_pantry = {"id", "status", "next_ask_on"}
    assert set(SeedItem.model_fields) == set(PantryItem.model_fields) - owned_by_the_pantry
