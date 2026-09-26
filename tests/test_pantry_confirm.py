"""SqlitePantry.confirm_stocked, the next_ask_on ask date and the seed's bootstrap reminder (B3).

Authority: `.context/seams/lane-b-pantry.md`, "Revision 4 — PR 2 (B3)" [Rev4], which wins over the
rest of that file; the `Pantry` Protocol docstrings in meals/contracts.py [Protocol]; and
`FakePantry.confirm_stocked`, the reference behaviour Revision 4 defers to [Fake]. The B3 dispatch
brief's items are cited as [A1]..[D]. Expected values are worked out by hand, never from the code.
Setup writes rows straight to the tables (`insert_item`, `_log`), not through the code under test.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from meals.contracts import PantryItem
from meals.pantry import SeedItem, SqlitePantry

Insert = Callable[[PantryItem], None]
Rows = list[tuple[Any, ...]]

# Not the day these tests were written (2026-09-26), so reading the clock instead of `on` fails.
ON = date(2026, 3, 2)
OLD = "2000-01-01 00:00:00"  # an updated_at that no real write can leave behind


def _on(days: int) -> date:
    return ON + timedelta(days=days)


def _item(id_: int, name: str, **fields: object) -> PantryItem:
    return PantryItem.model_validate({"id": id_, "name": name, "category": "staple", **fields})


def _seed(name: str, category: str = "staple", **fields: object) -> SeedItem:
    return SeedItem.model_validate({"name": name, "category": category, **fields})


def _log(db: sqlite3.Connection, item_id: int, on: date) -> None:
    db.execute("INSERT INTO purchase_log (item_id, purchased_on) VALUES (?, ?)", (item_id, str(on)))
    db.commit()


def _snapshot(db: sqlite3.Connection) -> tuple[Rows, Rows]:
    items = [tuple(row) for row in db.execute("SELECT * FROM pantry_item ORDER BY id")]
    return items, [tuple(row) for row in db.execute("SELECT * FROM purchase_log ORDER BY id")]


def _column(db: sqlite3.Connection, column: str) -> dict[str, Any]:
    """One column of every item, read straight from the table, by item name."""
    rows = db.execute(f"SELECT name, {column} FROM pantry_item ORDER BY id")
    return {row[0]: row[1] for row in rows}


# ── confirm_stocked: what it writes ([A1] [A2]) ───────────────────────────────


@pytest.mark.parametrize("plenty", [False, True], ids=["still good", "plenty"])
def test_confirming_an_unknown_item_returns_none_and_writes_nothing(
    db: sqlite3.Connection, insert_item: Insert, plenty: bool
) -> None:  # [A1]; [Protocol] "An unknown name returns None and writes nothing"
    insert_item(
        _item(1, "rice", status="buy_next_time", typical_interval_days=56, last_purchased=_on(-60))
    )
    before = _snapshot(db)
    assert SqlitePantry(db).confirm_stocked("saffron", ON, plenty=plenty) is None
    assert _snapshot(db) == before


@pytest.mark.parametrize("plenty", [False, True], ids=["still good", "plenty"])
def test_confirming_a_flagged_item_restocks_it_and_never_logs_a_purchase(
    db: sqlite3.Connection, insert_item: Insert, plenty: bool
) -> None:  # [A2]; [Rev4] "Status: always becomes `have`. It never writes `purchase_log`."
    insert_item(
        _item(
            1, "butter", status="buy_next_time", typical_interval_days=21, last_purchased=_on(-30)
        )
    )
    _log(db, 1, _on(-30))
    db.execute("UPDATE pantry_item SET updated_at = ?", (OLD,))
    db.commit()
    confirmed = SqlitePantry(db).confirm_stocked("butter", ON, plenty=plenty)
    assert confirmed is not None
    assert (confirmed.status, confirmed.last_purchased) == ("have", _on(-30))
    stored = db.execute("SELECT status, last_purchased, updated_at FROM pantry_item").fetchone()
    assert tuple(stored[:2]) == ("have", str(_on(-30)))
    assert stored[2] != OLD
    # The log is untouched: no purchase row for a reply that says nothing was bought.
    rows = [tuple(row) for row in db.execute("SELECT item_id, purchased_on FROM purchase_log")]
    assert rows == [(1, str(_on(-30)))]


# ── confirm_stocked: the push and the new ask date ([A3] [A4]) ────────────────


@pytest.mark.parametrize(
    ("fields", "plenty", "wait"),
    [
        ({"typical_interval_days": 42}, False, 7),
        ({"typical_interval_days": 42}, True, 42),
        ({}, False, 7),
        ({}, True, 7),
        # A purchase date with no interval: still no ask date, and nothing to grow
        ({"last_purchased": _on(-100)}, False, 7),
    ],
    ids=[
        "still good: a week",
        "plenty: one interval",
        "still good, no interval: a week",
        "plenty, no interval: a week",
        "still good, bought but no interval: a week",
    ],
)
def test_with_no_ask_date_still_good_asks_in_a_week_and_plenty_in_one_interval(
    db: sqlite3.Connection, insert_item: Insert, fields: dict[str, object], plenty: bool, wait: int
) -> None:  # [A3] [A4 "no ask date at all"]; [Rev4] "The push"; [Fake]
    insert_item(_item(1, "rice", **fields))
    expected = _item(1, "rice", **fields, next_ask_on=_on(wait))
    assert SqlitePantry(db).confirm_stocked("rice", ON, plenty=plenty) == expected
    assert SqlitePantry(db).get_item("rice") == expected
    assert _column(db, "next_ask_on") == {"rice": str(_on(wait))}


@pytest.mark.parametrize(
    ("fields", "plenty", "ask"),
    [
        ({"typical_interval_days": 42, "next_ask_on": _on(90)}, False, _on(90)),
        ({"typical_interval_days": 42, "next_ask_on": _on(90)}, True, _on(90)),
        ({"typical_interval_days": 42, "next_ask_on": _on(-3)}, False, _on(7)),
        ({"typical_interval_days": 42, "next_ask_on": _on(-3)}, True, _on(42)),
        # The 90% rule, not next_ask_on: 90% of 71 days is 63.9, rounded up to 64. Bought 5 days
        # ago, that's 59 days out, later than a week.
        ({"typical_interval_days": 71, "last_purchased": _on(-5)}, False, _on(59)),
        # ...and earlier than one interval (71), so plenty moves it.
        ({"typical_interval_days": 71, "last_purchased": _on(-5)}, True, _on(71)),
        # next_ask_on is the current ask date, not the later 90% point (59 days out): max(3, 7).
        (
            {"typical_interval_days": 71, "last_purchased": _on(-5), "next_ask_on": _on(3)},
            False,
            _on(7),
        ),
    ],
    ids=[
        "later next_ask_on kept (still good)",
        "later next_ask_on kept (plenty)",
        "earlier next_ask_on moved to the week (still good)",
        "earlier next_ask_on moved to the interval (plenty)",
        "later 90% point kept (still good)",
        "earlier 90% point moved to the interval (plenty)",
        "earlier next_ask_on beats a later 90% point (still good)",
    ],
)
def test_confirming_moves_the_ask_date_to_the_later_of_the_current_one_and_the_push(
    db: sqlite3.Connection, insert_item: Insert, fields: dict[str, object], plenty: bool, ask: date
) -> None:  # [A4]; [Rev4] "The new ask date"; [Fake] "this only ever pushes the ask back"
    insert_item(_item(1, "rice", **fields))
    expected = _item(1, "rice", **{**fields, "next_ask_on": ask})
    assert SqlitePantry(db).confirm_stocked("rice", ON, plenty=plenty) == expected
    assert SqlitePantry(db).get_item("rice") == expected
    assert _column(db, "next_ask_on") == {"rice": str(ask)}


# ── confirm_stocked: interval growth ([A5]) ───────────────────────────────────


@pytest.mark.parametrize(
    ("bought_days_ago", "interval", "ask_in"),
    [
        (100, 107, 7),  # lasted 100 days on a 60-day guess: 100 + 7
        (54, 61, 7),  # one day past: 54 + 7 = 61
        (53, 60, 7),  # 53 + 7 = 60, not more than the interval: kept
        (10, 60, 44),  # well inside; the 90% point (day 54, 44 days out) is later than a week
    ],
    ids=["grows to elapsed + 7", "grows by one", "equal: kept", "inside: kept"],
)
def test_still_good_on_a_staple_grows_its_interval_to_the_days_it_has_lasted_plus_a_week(
    db: sqlite3.Connection, insert_item: Insert, bought_days_ago: int, interval: int, ask_in: int
) -> None:  # [A5]; [Rev4] "Interval growth"
    bought = _on(-bought_days_ago)
    insert_item(_item(1, "olive oil", typical_interval_days=60, last_purchased=bought))
    expected = _item(
        1,
        "olive oil",
        typical_interval_days=interval,
        last_purchased=bought,
        next_ask_on=_on(ask_in),
    )
    assert SqlitePantry(db).confirm_stocked("olive oil", ON) == expected
    assert SqlitePantry(db).get_item("olive oil") == expected
    assert _column(db, "typical_interval_days") == {"olive oil": interval}


@pytest.mark.parametrize(
    ("category", "plenty", "wait"),
    [("staple", True, 14), ("perishable", False, 7), ("fallback", False, 7)],
    ids=["plenty on a staple", "still good on a perishable", "still good on a fallback"],
)
def test_only_still_good_on_a_staple_changes_the_interval(
    db: sqlite3.Connection, insert_item: Insert, category: str, plenty: bool, wait: int
) -> None:  # [A5]; [Rev4] "Plenty never touches the interval, and neither reply touches a
    # non-staple's" (a fallback's would ratchet upward forever)
    fields: dict[str, object] = {
        "category": category,
        "typical_interval_days": 14,
        "last_purchased": _on(-100),  # elapsed + 7 = 107 if it wrongly grew
    }
    insert_item(_item(1, "nuggets", **fields))
    expected = _item(1, "nuggets", **fields, next_ask_on=_on(wait))
    assert SqlitePantry(db).confirm_stocked("nuggets", ON, plenty=plenty) == expected
    assert SqlitePantry(db).get_item("nuggets") == expected
    assert _column(db, "next_ask_on") == {"nuggets": str(_on(wait))}


# ── confirm_stocked: replays, lookup, datetimes ([A6] [A7] [A8]) ──────────────


@pytest.mark.parametrize("plenty", [False, True], ids=["still good", "plenty"])
def test_confirming_twice_for_the_same_day_changes_nothing_the_second_time(
    db: sqlite3.Connection, insert_item: Insert, plenty: bool
) -> None:  # [A6]; [Rev4] "Idempotent for the same `on`, on every field except updated_at"
    insert_item(
        _item(
            1,
            "olive oil",
            status="buy_next_time",
            typical_interval_days=60,
            last_purchased=_on(-100),
        )
    )
    pantry = SqlitePantry(db)
    first = pantry.confirm_stocked("olive oil", ON, plenty=plenty)
    assert first is not None
    assert first.next_ask_on is not None  # the first call wrote something
    assert pantry.confirm_stocked("olive oil", ON, plenty=plenty) == first
    assert SqlitePantry(db).get_item("olive oil") == first
    assert db.execute("SELECT COUNT(*) FROM purchase_log").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["EVOO", "  Olive Oil "])
def test_confirm_stocked_finds_the_item_by_name_or_alias_ignoring_case_and_spaces(
    db: sqlite3.Connection, insert_item: Insert, name: str
) -> None:  # [A7]; [Protocol] "matches it against item names and aliases after strip().casefold()"
    insert_item(_item(1, "olive oil", aliases=("evoo",), status="buy_next_time"))
    confirmed = SqlitePantry(db).confirm_stocked(name, ON)
    assert confirmed is not None
    assert confirmed.name == "olive oil"
    assert _column(db, "status") == {"olive oil": "have"}


def test_confirming_by_name_never_touches_another_item_that_has_it_as_an_alias(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [A7]; [Protocol] "an exact name beats another item's alias"
    insert_item(_item(1, "brown rice", aliases=("rice",), status="buy_next_time"))
    insert_item(_item(2, "rice", status="buy_next_time"))
    confirmed = SqlitePantry(db).confirm_stocked("RICE", ON)
    assert confirmed is not None
    assert confirmed.id == 2
    assert _column(db, "status") == {"brown rice": "buy_next_time", "rice": "have"}


def test_confirming_with_a_datetime_uses_its_calendar_date(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [A8]; [Rev4] "A datetime `on` is reduced to its date"
    insert_item(_item(1, "olive oil", typical_interval_days=60, last_purchased=_on(-100)))
    confirmed = SqlitePantry(db).confirm_stocked("olive oil", datetime(2026, 3, 2, 23, 30))
    assert confirmed == _item(
        1, "olive oil", typical_interval_days=107, last_purchased=_on(-100), next_ask_on=_on(7)
    )
    stored = db.execute("SELECT next_ask_on, typical_interval_days FROM pantry_item").fetchone()
    assert tuple(stored) == ("2026-03-09", 107)


# ── staples_due: the ask date honours next_ask_on ([B]) ───────────────────────


@pytest.mark.parametrize(
    ("fields", "due"),
    [
        # The 90% rule says due (66 days past), next_ask_on says tomorrow.
        (
            {"typical_interval_days": 60, "last_purchased": _on(-120), "next_ask_on": _on(1)},
            False,
        ),
        # The 90% rule says 50 days to go, next_ask_on says today.
        ({"typical_interval_days": 56, "last_purchased": _on(-1), "next_ask_on": ON}, True),
        # A bootstrap reminder alone, on its day and the day before.
        ({"next_ask_on": ON}, True),
        ({"next_ask_on": _on(1)}, False),  # baseline-green: no ask date today either
        # A flag still makes it due. Baseline-green: flags already do.
        ({"status": "buy_next_time", "next_ask_on": _on(30)}, True),
        # Only staples are asked about. Baseline-green.
        ({"category": "perishable", "next_ask_on": _on(-5)}, False),
    ],
    ids=[
        "90% rule due, next_ask_on tomorrow",
        "90% rule not due, next_ask_on today",
        "reminder alone, its day",
        "reminder alone, the day before",
        "flag beats a later next_ask_on",
        "never a perishable",
    ],
)
def test_a_staple_with_next_ask_on_is_due_from_that_day_whatever_the_90_percent_rule_says(
    db: sqlite3.Connection, insert_item: Insert, fields: dict[str, object], due: bool
) -> None:  # [B]; [Rev4] `_ask_date`; [Protocol] the ask date is next_ask_on when set
    insert_item(_item(1, "tahini", **fields))
    assert [item.name for item in SqlitePantry(db).staples_due(ON)] == (["tahini"] if due else [])


def test_staples_due_orders_by_days_past_next_ask_on_when_it_is_set(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [B]; [Protocol] staples_due: flagged first (those with no ask date last among them),
    # then most days past the ask date, then name
    items: list[tuple[str, dict[str, object]]] = [
        ("Zaatar", {"next_ask_on": _on(-2)}),  # 2 days past
        ("allspice", {"next_ask_on": _on(-2)}),  # 2 days past; the name breaks the tie
        ("salt", {"next_ask_on": _on(-9)}),  # 9 days past
        # 1 day past next_ask_on; its 90% point would make it 66 days past
        (
            "tahini",
            {"typical_interval_days": 60, "last_purchased": _on(-120), "next_ask_on": _on(-1)},
        ),
        ("rice", {"typical_interval_days": 56, "last_purchased": _on(-55)}),  # 90% rule: 4 past
        ("honey", {"status": "buy_next_time"}),  # flagged, no ask date
        ("yeast", {"status": "buy_next_time", "next_ask_on": _on(14)}),  # flagged, 14 days early
        ("oats", {"status": "buy_next_time", "next_ask_on": _on(-1)}),  # flagged, 1 day past
    ]
    for id_, (name, fields) in enumerate(items, start=1):
        insert_item(_item(id_, name, **fields))
    names = [item.name for item in SqlitePantry(db).staples_due(ON)]
    assert names == ["oats", "yeast", "honey", "salt", "rice", "allspice", "Zaatar", "tahini"]


# ── log_purchase and next_ask_on ([C]) ────────────────────────────────────────


@pytest.mark.parametrize(
    "last_purchased", [_on(-30), ON, None], ids=["bought before", "same day, not logged", "never"]
)
def test_the_latest_purchase_clears_next_ask_on(
    db: sqlite3.Connection, insert_item: Insert, last_purchased: date | None
) -> None:  # [C]; [Rev4] "`log_purchase`"; [Protocol] log_purchase "clears `next_ask_on`"
    insert_item(
        _item(
            1,
            "tahini",
            typical_interval_days=60,
            last_purchased=last_purchased,
            next_ask_on=_on(10),
        )
    )
    expected = _item(1, "tahini", typical_interval_days=60, last_purchased=ON)
    assert SqlitePantry(db).log_purchase("tahini", ON) == expected
    assert SqlitePantry(db).get_item("tahini") == expected
    assert _column(db, "next_ask_on") == {"tahini": None}


def test_a_back_dated_purchase_leaves_next_ask_on_alone(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [C]; baseline-green: log_purchase doesn't touch next_ask_on yet
    fields: dict[str, object] = {
        "status": "buy_next_time",
        "typical_interval_days": 60,
        "last_purchased": ON,
        "next_ask_on": _on(10),
    }
    insert_item(_item(1, "tahini", **fields))
    assert SqlitePantry(db).log_purchase("tahini", _on(-12)) == _item(1, "tahini", **fields)
    assert _column(db, "next_ask_on") == {"tahini": str(_on(10))}
    # It was still written, as history.
    rows = [tuple(row) for row in db.execute("SELECT item_id, purchased_on FROM purchase_log")]
    assert rows == [(1, str(_on(-12)))]


def test_a_replayed_purchase_keeps_a_next_ask_on_set_after_it(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [C]; [Protocol] "a repeat for the same day is a replay and writes nothing".
    # Baseline-green: a replay already writes nothing. Bought today, then "still good" today set
    # next_ask_on; the replayed "ordered" message must not clear it.
    fields: dict[str, object] = {
        "typical_interval_days": 60,
        "last_purchased": ON,
        "next_ask_on": _on(7),
    }
    insert_item(_item(1, "tahini", **fields))
    _log(db, 1, ON)
    before = _snapshot(db)
    assert SqlitePantry(db).log_purchase("tahini", ON, qty=1) == _item(1, "tahini", **fields)
    assert _snapshot(db) == before


# ── load_seed: the bootstrap reminder ([D]) ───────────────────────────────────


def test_the_seed_gives_each_owned_staple_without_a_purchase_date_a_reminder_at_90_percent(
    db: sqlite3.Connection, insert_item: Insert
) -> None:  # [D]; [Rev4] "The bootstrap reminder"
    insert_item(_item(1, "tortillas", category="perishable"))
    insert_item(_item(2, "rice", typical_interval_days=56))
    insert_item(_item(3, "paprika", typical_interval_days=30, next_ask_on=_on(-40)))
    insert_item(_item(4, "tahini", typical_interval_days=60))
    insert_item(_item(5, "cumin", typical_interval_days=30))  # would qualify, but isn't in the seed
    insert_item(_item(6, "honey", typical_interval_days=30, last_purchased=_on(-10)))
    insert_item(_item(7, "cinnamon", typical_interval_days=40))
    SqlitePantry(db).load_seed(
        [
            _seed("tortillas", typical_interval_days=60),  # promoted to staple
            _seed("rice"),  # blank interval: keeps its 56
            _seed("paprika", typical_interval_days=30),  # already has a reminder
            _seed("tahini", "fallback", typical_interval_days=60),  # demoted
            _seed("honey", typical_interval_days=30),  # an existing staple with a purchase date
            # The seed never sets an existing item's purchase date, so cinnamon stays undated.
            _seed("cinnamon", typical_interval_days=40, last_purchased=_on(-3)),
            _seed("couscous", typical_interval_days=60),  # new, already in the house
            _seed("olive oil", typical_interval_days=70, last_purchased=_on(-10)),  # a real date
            _seed("salt"),  # no interval: no basis for a reminder
            _seed("eggs", "perishable", typical_interval_days=14),
            _seed("nuggets", "fallback", typical_interval_days=14),
        ],
        ON,
    )
    assert _column(db, "next_ask_on") == {
        "tortillas": str(_on(54)),  # 90% of 60 days
        "rice": str(_on(51)),  # 90% of 56 is 50.4, rounded up
        "paprika": str(_on(-40)),  # never moved
        "tahini": None,
        "cumin": None,  # untouched items are never changed
        "honey": None,  # its purchase date gives it an ask date already
        "cinnamon": str(_on(36)),  # 90% of 40 days
        "couscous": str(_on(54)),
        "olive oil": None,  # its purchase date gives it an ask date already
        "salt": None,
        "eggs": None,
        "nuggets": None,
    }
    # No purchase date is invented: honey keeps its own, olive oil's real one is stored and logged,
    # and cinnamon's seed date is neither stored nor logged.
    bought = {name: day for name, day in _column(db, "last_purchased").items() if day is not None}
    assert bought == {"honey": str(_on(-10)), "olive oil": str(_on(-10))}
    joined = db.execute(
        "SELECT i.name, l.purchased_on FROM purchase_log l JOIN pantry_item i ON i.id = l.item_id"
    )
    assert [tuple(row) for row in joined] == [("olive oil", str(_on(-10)))]


def test_running_the_seed_again_on_a_later_day_never_moves_its_reminder(
    db: sqlite3.Connection,
) -> None:  # [D]; [Rev4] "A re-run never moves an existing reminder"
    seed = [_seed("couscous", typical_interval_days=60)]
    pantry = SqlitePantry(db)
    pantry.load_seed(seed, ON)
    assert _column(db, "next_ask_on") == {"couscous": str(_on(54))}
    pantry.load_seed(seed, _on(30))
    assert _column(db, "next_ask_on") == {"couscous": str(_on(54))}
