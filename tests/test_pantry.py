import sqlite3
from collections.abc import Callable
from datetime import date, timedelta

import pytest

from meals.contracts import Pantry, PantryItem
from meals.fakes import FakePantry
from meals.pantry import SqlitePantry

TODAY = date(2026, 9, 26)

Insert = Callable[[PantryItem], None]


def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def _item(id_: int, name: str, category: str = "staple", **fields: object) -> PantryItem:
    return PantryItem.model_validate({"id": id_, "name": name, "category": category, **fields})


@pytest.fixture
def pantry(
    db: sqlite3.Connection, insert_item: Insert, sample_pantry_items: tuple[PantryItem, ...]
) -> SqlitePantry:
    for item in sample_pantry_items:
        insert_item(item)
    return SqlitePantry(db)


def _row(db: sqlite3.Connection, name: str) -> sqlite3.Row:
    row: sqlite3.Row = db.execute("SELECT * FROM pantry_item WHERE name = ?", (name,)).fetchone()
    return row


# ── reading ──────────────────────────────────────────────────────────────────


def test_satisfies_the_pantry_protocol(pantry: SqlitePantry) -> None:
    assert isinstance(pantry, Pantry)


def test_list_items_round_trips_every_field(
    pantry: SqlitePantry, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    assert pantry.list_items() == sample_pantry_items


def test_list_items_round_trips_product_fields(db: sqlite3.Connection, insert_item: Insert) -> None:
    item = _item(
        1,
        "siete taco shells",
        aliases=("taco shells", "siete shells"),
        default_qty=1,
        default_unit="box",
        meijer_product_id="708820",
        meijer_url="https://www.meijer.com/shopping/product/siete-taco-shells/708820.html",
        preferred_product_name="Siete Grain Free Taco Shells, 5.5 oz",
        substitute_ok=False,
        for_miles=True,
        notes="the blue box",
    )
    insert_item(item)
    assert SqlitePantry(db).list_items() == (item,)


def test_a_stored_non_meijer_url_fails_closed_on_read(db: sqlite3.Connection) -> None:
    # Review finding 12: this text reaches Steve through the Saturday job's failure message, so it
    # names the row (id and name) and the fix. Imported here so only this test is RED until then.
    from meals.pantry import PantryRowError

    db.execute(
        "INSERT INTO pantry_item (id, name, category, meijer_url) VALUES (?, ?, ?, ?)",
        (4127, "rice", "staple", "https://evil.example/grain"),
    )
    db.commit()
    assert issubclass(PantryRowError, ValueError)
    with pytest.raises(PantryRowError, match=r"(?is)(?=.*\b4127\b)(?=.*\brice\b)(?=.*seed loader)"):
        SqlitePantry(db).list_items()


@pytest.mark.parametrize("name", ["olive oil", "Olive Oil", "  olive oil ", "EVOO", "evoo"])
def test_get_item_matches_name_or_alias_ignoring_case_and_spaces(
    pantry: SqlitePantry, name: str
) -> None:
    item = pantry.get_item(name)
    assert item is not None
    assert item.name == "olive oil"


def test_get_item_returns_none_for_an_unknown_name(pantry: SqlitePantry) -> None:
    assert pantry.get_item("saffron") is None


def test_a_name_match_wins_over_another_items_alias(
    db: sqlite3.Connection, insert_item: Insert
) -> None:
    insert_item(_item(1, "brown rice", aliases=("rice",)))
    insert_item(_item(2, "rice"))
    item = SqlitePantry(db).get_item("rice")
    assert item is not None
    assert item.id == 2


# ── staples_due ──────────────────────────────────────────────────────────────


def test_staples_due_matches_the_fake_on_the_shared_fixture(
    pantry: SqlitePantry, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    due = pantry.staples_due(TODAY)
    assert [item.name for item in due] == ["butter", "tahini", "olive oil"]
    assert due == FakePantry(sample_pantry_items).staples_due(TODAY)


@pytest.mark.parametrize(("elapsed", "due"), [(62, False), (63, True)])
def test_a_staple_is_due_at_exactly_90_percent_of_its_interval(
    db: sqlite3.Connection, insert_item: Insert, elapsed: int, due: bool
) -> None:
    insert_item(
        _item(1, "rice", typical_interval_days=70, last_purchased=_ago(elapsed)),
    )
    assert bool(SqlitePantry(db).staples_due(TODAY)) is due


def test_only_staples_are_ever_due(db: sqlite3.Connection, insert_item: Insert) -> None:
    for id_, category in enumerate(("perishable", "fallback"), start=1):
        insert_item(
            _item(
                id_,
                category,
                category,
                status="buy_next_time",
                typical_interval_days=7,
                last_purchased=_ago(365),
            ),
        )
    assert SqlitePantry(db).staples_due(TODAY) == ()


def test_a_staple_without_interval_or_purchase_date_is_due_only_when_flagged(
    db: sqlite3.Connection, insert_item: Insert
) -> None:
    insert_item(_item(1, "tahini", last_purchased=_ago(365)))
    insert_item(_item(2, "rice", typical_interval_days=7))
    insert_item(_item(3, "couscous", status="buy_next_time"))
    assert [item.name for item in SqlitePantry(db).staples_due(TODAY)] == ["couscous"]


def test_staples_due_orders_flagged_first_then_most_overdue_then_by_name(
    db: sqlite3.Connection, insert_item: Insert
) -> None:
    insert_item(_item(1, "rice", typical_interval_days=10, last_purchased=_ago(20)))
    insert_item(_item(2, "couscous", typical_interval_days=10, last_purchased=_ago(20)))
    insert_item(_item(3, "tahini", typical_interval_days=10, last_purchased=_ago(30)))
    insert_item(_item(4, "butter", status="buy_next_time"))
    insert_item(_item(5, "olive oil", status="buy_next_time", typical_interval_days=10,
                      last_purchased=_ago(5)))  # fmt: skip
    names = [item.name for item in SqlitePantry(db).staples_due(TODAY)]
    assert names == ["olive oil", "butter", "tahini", "couscous", "rice"]


# ── flip_status ──────────────────────────────────────────────────────────────


def test_flip_status_by_alias_returns_and_persists_the_change(
    pantry: SqlitePantry, db: sqlite3.Connection
) -> None:
    db.execute("UPDATE pantry_item SET updated_at = '2000-01-01 00:00:00' WHERE name = 'eggs'")
    db.commit()
    flipped = pantry.flip_status("Egg", "buy_next_time")
    assert flipped is not None
    assert (flipped.name, flipped.status) == ("eggs", "buy_next_time")
    row = _row(db, "eggs")
    assert row["status"] == "buy_next_time"
    assert row["updated_at"] != "2000-01-01 00:00:00"
    assert SqlitePantry(db).get_item("eggs") == flipped


def test_flip_status_of_an_unknown_item_returns_none_and_writes_nothing(
    pantry: SqlitePantry, db: sqlite3.Connection
) -> None:
    before = [tuple(row) for row in db.execute("SELECT * FROM pantry_item ORDER BY id")]
    assert pantry.flip_status("saffron", "buy_next_time") is None
    assert [tuple(row) for row in db.execute("SELECT * FROM pantry_item ORDER BY id")] == before
