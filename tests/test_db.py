"""meals.db: connection setup and schema migrations.

Authority: the db.py docstrings, PLAN.md (Table schema; Runtime concurrency), and the seam map
(`get_db()` / `migrate()` / `MIGRATIONS`). The expected schema below is transcribed by hand from
PLAN.md, not read back from the code.
"""

import sqlite3
import threading
from pathlib import Path
from typing import get_args

import pytest

import meals.db
from meals.contracts import PantryCategory, PantryStatus

pytestmark = pytest.mark.usefixtures("isolated_settings")

Column = tuple[str, int, str | None, int]  # declared type, notnull, default, pk

# PLAN.md, Table schema: column -> (declared type, NOT NULL, DEFAULT, PRIMARY KEY).
PLAN_SCHEMA: dict[str, dict[str, Column]] = {
    "pantry_item": {
        "id": ("INTEGER", 0, None, 1),
        "name": ("TEXT", 1, None, 0),
        "aliases": ("TEXT", 0, None, 0),
        "category": ("TEXT", 1, None, 0),
        "status": ("TEXT", 1, "'have'", 0),
        "typical_interval_days": ("INTEGER", 0, None, 0),
        "last_purchased": ("DATE", 0, None, 0),
        "default_qty": ("REAL", 0, None, 0),
        "default_unit": ("TEXT", 0, None, 0),
        "meijer_product_id": ("TEXT", 0, None, 0),
        "meijer_url": ("TEXT", 0, None, 0),
        "preferred_product_name": ("TEXT", 0, None, 0),
        "substitute_ok": ("INTEGER", 1, "1", 0),
        "for_miles": ("INTEGER", 1, "0", 0),
        "notes": ("TEXT", 0, None, 0),
        "updated_at": ("DATETIME", 1, "CURRENT_TIMESTAMP", 0),
    },
    "purchase_log": {
        "id": ("INTEGER", 0, None, 1),
        "item_id": ("INTEGER", 1, None, 0),
        "purchased_on": ("DATE", 1, None, 0),
        "qty": ("REAL", 0, None, 0),
        "price_cents": ("INTEGER", 0, None, 0),
        "source": ("TEXT", 0, "'meijer_pickup'", 0),
    },
    "weekly_plan": {
        "id": ("INTEGER", 0, None, 1),
        "week_start": ("DATE", 1, None, 0),
        "custody": ("TEXT", 0, None, 0),
        "components": ("TEXT", 1, None, 0),
        "status": ("TEXT", 1, "'proposed'", 0),
        "mealie_plan_ref": ("TEXT", 0, None, 0),
        "approved_at": ("DATETIME", 0, None, 0),
        "notes": ("TEXT", 0, None, 0),
    },
    "meal_rating": {
        "id": ("INTEGER", 0, None, 1),
        "week_start": ("DATE", 1, None, 0),
        "component": ("TEXT", 1, None, 0),
        "rater": ("TEXT", 1, None, 0),
        "rating": ("INTEGER", 1, None, 0),
        "rated_at": ("DATETIME", 1, "CURRENT_TIMESTAMP", 0),
    },
}

# The smallest valid row per table; tests override one column at a time.
BASE_ROWS: dict[str, dict[str, object]] = {
    "pantry_item": {"name": "olive oil", "category": "staple"},
    "weekly_plan": {"week_start": "2026-09-27", "components": "{}"},
    "meal_rating": {
        "week_start": "2026-09-27",
        "component": "shredded chicken",
        "rater": "steve",
        "rating": 1,
    },
}

PROBE = ("CREATE TABLE probe (x INTEGER)", "INSERT INTO probe (x) VALUES (42)")


def _insert(conn: sqlite3.Connection, table: str, **overrides: object) -> None:
    row = BASE_ROWS[table] | overrides
    placeholders = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({placeholders})", tuple(row.values())
    )


def _version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def _column(conn: sqlite3.Connection, sql: str) -> list[object]:
    return [row[0] for row in conn.execute(sql).fetchall()]


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


# ── schema ───────────────────────────────────────────────────────────────────


def test_migration_1_creates_exactly_the_plan_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(meals.db, "MIGRATIONS", meals.db.MIGRATIONS[:1])

    conn = meals.db.get_db(tmp_path / "pantry.sqlite")

    assert _version(conn) == 1
    assert _tables(conn) == set(PLAN_SCHEMA)
    for table, expected in PLAN_SCHEMA.items():
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual = {row[1]: (row[2].upper(), row[3], row[4], row[5]) for row in info}
        assert actual == expected, table


@pytest.mark.parametrize(
    ("table", "column"), [("pantry_item", "name"), ("weekly_plan", "week_start")]
)
def test_unique_columns(table: str, column: str, tmp_path: Path) -> None:
    conn = meals.db.get_db(tmp_path / "pantry.sqlite")
    _insert(conn, table)

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(conn, table)
    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


ACCEPTED = [
    *(("pantry_item", "category", value) for value in get_args(PantryCategory)),
    *(("pantry_item", "status", value) for value in get_args(PantryStatus)),
    *(("weekly_plan", "status", v) for v in ("proposed", "approved", "cart_filled", "ordered")),
    *(("meal_rating", "rater", value) for value in ("steve", "miles")),
    *(("meal_rating", "rating", value) for value in (-1, 1)),
]

REJECTED = [
    ("pantry_item", "category", "frozen"),
    ("pantry_item", "status", "out"),
    ("weekly_plan", "status", "cancelled"),
    ("meal_rating", "rater", "guest"),
    ("meal_rating", "rating", 0),
    ("meal_rating", "rating", 2),
]


@pytest.mark.parametrize(("table", "column", "value"), ACCEPTED)
def test_check_constraints_accept_every_allowed_value(
    table: str, column: str, value: object, tmp_path: Path
) -> None:
    """pantry_item values come from the contract Literals: a new Literal value needs a migration."""
    conn = meals.db.get_db(tmp_path / "pantry.sqlite")

    _insert(conn, table, **{column: value})

    assert conn.execute(f"SELECT {column} FROM {table}").fetchone()[0] == value


@pytest.mark.parametrize(("table", "column", "value"), REJECTED)
def test_check_constraints_reject_other_values(
    table: str, column: str, value: object, tmp_path: Path
) -> None:
    conn = meals.db.get_db(tmp_path / "pantry.sqlite")

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _insert(conn, table, **{column: value})
    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """SQLite ignores REFERENCES unless PRAGMA foreign_keys is on for the connection."""
    conn = meals.db.get_db(tmp_path / "pantry.sqlite")
    _insert(conn, "pantry_item")
    (item_id,) = conn.execute("SELECT id FROM pantry_item").fetchone()
    log = "INSERT INTO purchase_log (item_id, purchased_on) VALUES (?, '2026-09-20')"

    conn.execute(log, (item_id,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(log, (item_id + 999,))
    assert _column(conn, "SELECT item_id FROM purchase_log") == [item_id]


# ── connection settings ──────────────────────────────────────────────────────


def test_connection_uses_wal_row_factory_and_a_busy_timeout(tmp_path: Path) -> None:
    conn = meals.db.get_db(tmp_path / "pantry.sqlite")

    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    row = conn.execute("SELECT 7 AS answer").fetchone()
    assert isinstance(row, sqlite3.Row)
    assert row["answer"] == 7


def test_get_db_defaults_to_settings_pantry_db_and_creates_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state" / "nested" / "pantry.sqlite"
    monkeypatch.setenv("PANTRY_DB", str(target))

    conn = meals.db.get_db()
    conn.close()

    assert target.is_file()
    check = sqlite3.connect(target)
    assert _version(check) == len(meals.db.MIGRATIONS)
    check.close()


def test_db_fixture_is_a_migrated_database(db: sqlite3.Connection) -> None:
    assert _version(db) == len(meals.db.MIGRATIONS)
    assert set(PLAN_SCHEMA) <= _tables(db)


# ── migrations ───────────────────────────────────────────────────────────────


def test_reopening_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "pantry.sqlite"
    conn = meals.db.get_db(path)
    _insert(conn, "pantry_item", name="tahini")
    conn.commit()
    conn.close()

    reopened = meals.db.get_db(path)

    assert _version(reopened) == len(meals.db.MIGRATIONS)
    assert meals.db.migrate(reopened) == len(meals.db.MIGRATIONS)
    assert _column(reopened, "SELECT name FROM pantry_item") == ["tahini"]


def test_appended_migration_is_applied_once_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pantry.sqlite"
    meals.db.get_db(path).close()
    latest = len(meals.db.MIGRATIONS)
    monkeypatch.setattr(meals.db, "MIGRATIONS", (*meals.db.MIGRATIONS, PROBE))

    conn = meals.db.get_db(path)

    assert _version(conn) == latest + 1
    assert meals.db.migrate(conn) == latest + 1
    assert _column(conn, "SELECT x FROM probe") == [42]


def test_failing_migration_rolls_back_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second statement that fails must take the first one's CREATE TABLE with it."""
    path = tmp_path / "pantry.sqlite"
    conn = meals.db.get_db(path)
    latest = len(meals.db.MIGRATIONS)
    broken = ("CREATE TABLE probe (x INTEGER)", "THIS IS NOT SQL")
    monkeypatch.setattr(meals.db, "MIGRATIONS", (*meals.db.MIGRATIONS, broken))

    try:
        meals.db.migrate(conn)
    except Exception:  # the type is unspecified; what matters is what's left behind
        pass
    else:
        pytest.fail("migrate() returned normally despite a statement that isn't SQL")

    assert _version(conn) == latest
    assert "probe" not in _tables(conn)
    conn.close()
    fresh = sqlite3.connect(path)
    assert _version(fresh) == latest
    assert "probe" not in _tables(fresh)
    fresh.close()


def test_first_open_waits_out_a_writer_holding_a_fresh_file(tmp_path: Path) -> None:
    """Switching a fresh file to WAL reports "database is locked" at once, without honouring the
    busy timeout, while another connection holds a lock. get_db must wait it out, not fail."""
    path = tmp_path / "pantry.sqlite"
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, holder.execute, ("ROLLBACK",))
    release.start()
    try:
        conn = meals.db.get_db(path)
    finally:
        release.join()
        holder.close()

    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert _version(conn) == len(meals.db.MIGRATIONS)
    conn.close()


def test_concurrent_first_opens_apply_each_migration_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several connections race to migrate one fresh file; nobody may double-apply.

    The migration is deliberately slow (200k-row insert) so every racer reads user_version while
    the winner is still inside its transaction. An implementation that reads user_version before
    BEGIN IMMEDIATE then re-runs CREATE TABLE and fails with "table probe already exists".
    """
    rows = 200_000
    slow = (
        "CREATE TABLE probe (x INTEGER)",
        "INSERT INTO probe (x) WITH RECURSIVE n(x) AS "
        f"(SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < {rows}) SELECT x FROM n",
    )
    monkeypatch.setattr(meals.db, "MIGRATIONS", (slow,))
    path = tmp_path / "pantry.sqlite"
    racers = 4
    barrier = threading.Barrier(racers)
    errors: list[BaseException] = []
    versions: list[int] = []

    def open_db() -> None:
        barrier.wait(timeout=30)
        try:
            conn = meals.db.get_db(path)
        except BaseException as exc:
            errors.append(exc)
            return
        versions.append(_version(conn))
        conn.close()

    threads = [threading.Thread(target=open_db) for _ in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert versions == [1] * racers
    check = sqlite3.connect(path)
    assert check.execute("SELECT count(*) FROM probe").fetchone()[0] == rows
    check.close()
