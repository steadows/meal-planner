"""meals.db: connection setup and schema migrations.

Authority: the db.py docstrings, PLAN.md (Table schema; Runtime concurrency), and the seam map
(`get_db()` / `migrate()` / `MIGRATIONS`). The expected schema below is transcribed by hand from
PLAN.md, not read back from the code.
"""

import logging
import re
import sqlite3
import threading
from contextlib import closing
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


def _open(tmp_path: Path) -> closing[sqlite3.Connection]:
    """A migrated database on tmp_path, closed when the with-block ends."""
    return closing(meals.db.get_db(tmp_path / "pantry.sqlite"))


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

    with _open(tmp_path) as conn:
        assert _version(conn) == 1
        assert _tables(conn) == set(PLAN_SCHEMA)
        for table, expected in PLAN_SCHEMA.items():
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            actual = {row[1]: (row[2].upper(), row[3], row[4], row[5]) for row in info}
            assert actual == expected, table


def test_migration_2_adds_next_ask_on_and_changes_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seam map contracts-3-followup, Migration 2: a nullable DATE with no default, plus an index."""
    monkeypatch.setattr(meals.db, "MIGRATIONS", meals.db.MIGRATIONS[:2])
    expected_schema = PLAN_SCHEMA | {
        "pantry_item": PLAN_SCHEMA["pantry_item"] | {"next_ask_on": ("DATE", 0, None, 0)}
    }

    with _open(tmp_path) as conn:
        assert _version(conn) == 2
        assert _tables(conn) == set(expected_schema)
        for table, expected in expected_schema.items():
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            actual = {row[1]: (row[2].upper(), row[3], row[4], row[5]) for row in info}
            assert actual == expected, table


@pytest.mark.parametrize(
    ("table", "column"), [("pantry_item", "name"), ("weekly_plan", "week_start")]
)
def test_unique_columns(table: str, column: str, tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        _insert(conn, table)

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert(conn, table)
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_pantry_item_name_is_unique_ignoring_case(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        _insert(conn, "pantry_item", name="olive oil")

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert(conn, "pantry_item", name="Olive Oil")


ACCEPTED = [
    *(("pantry_item", "category", value) for value in get_args(PantryCategory)),
    *(("pantry_item", "status", value) for value in get_args(PantryStatus)),
    *(("weekly_plan", "status", v) for v in ("proposed", "approved", "cart_filled", "ordered")),
    *(("meal_rating", "rater", value) for value in ("steve", "miles")),
    *(("meal_rating", "rating", value) for value in (-1, 1)),
    *(("pantry_item", "typical_interval_days", value) for value in (None, 1, 70)),
]

REJECTED = [
    ("pantry_item", "category", "frozen"),
    ("pantry_item", "status", "out"),
    ("weekly_plan", "status", "cancelled"),
    ("meal_rating", "rater", "guest"),
    ("meal_rating", "rating", 0),
    ("meal_rating", "rating", 2),
    ("pantry_item", "typical_interval_days", 0),
    ("pantry_item", "typical_interval_days", -7),
]


@pytest.mark.parametrize(("table", "column", "value"), ACCEPTED)
def test_check_constraints_accept_every_allowed_value(
    table: str, column: str, value: object, tmp_path: Path
) -> None:
    """pantry_item values come from the contract Literals: a new Literal value needs a migration."""
    with _open(tmp_path) as conn:
        _insert(conn, table, **{column: value})

        assert conn.execute(f"SELECT {column} FROM {table}").fetchone()[0] == value


@pytest.mark.parametrize(("table", "column", "value"), REJECTED)
def test_check_constraints_reject_other_values(
    table: str, column: str, value: object, tmp_path: Path
) -> None:
    with _open(tmp_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _insert(conn, table, **{column: value})
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """SQLite ignores REFERENCES unless PRAGMA foreign_keys is on for the connection."""
    log = "INSERT INTO purchase_log (item_id, purchased_on) VALUES (?, '2026-09-20')"
    with _open(tmp_path) as conn:
        _insert(conn, "pantry_item")
        (item_id,) = conn.execute("SELECT id FROM pantry_item").fetchone()

        conn.execute(log, (item_id,))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(log, (item_id + 999,))
        assert _column(conn, "SELECT item_id FROM purchase_log") == [item_id]


LOG_PURCHASE = "INSERT INTO purchase_log (item_id, purchased_on) VALUES (?, ?)"


def test_purchase_log_holds_one_row_per_item_per_day(tmp_path: Path) -> None:
    """Migration 2's UNIQUE (item_id, purchased_on): per item and day, not per item or per day."""
    with _open(tmp_path) as conn:
        _insert(conn, "pantry_item", name="olive oil")
        _insert(conn, "pantry_item", name="tahini")
        oil, tahini = _column(conn, "SELECT id FROM pantry_item ORDER BY id")
        conn.execute(LOG_PURCHASE, (oil, "2026-09-20"))
        conn.execute(LOG_PURCHASE, (oil, "2026-09-27"))
        conn.execute(LOG_PURCHASE, (tahini, "2026-09-20"))

        # The key leaves out `source` on purpose: a second source is still the same day's purchase.
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO purchase_log (item_id, purchased_on, source) VALUES (?, ?, 'manual')",
                (oil, "2026-09-20"),
            )
        rows = conn.execute("SELECT item_id, purchased_on FROM purchase_log ORDER BY id")
        assert [tuple(row) for row in rows] == [
            (oil, "2026-09-20"),
            (oil, "2026-09-27"),
            (tahini, "2026-09-20"),
        ]


# ── connection settings ──────────────────────────────────────────────────────


def test_connection_uses_wal_row_factory_and_a_busy_timeout(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
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

    meals.db.get_db().close()

    assert target.is_file()
    with closing(sqlite3.connect(target)) as check:
        assert _version(check) == len(meals.db.MIGRATIONS)


def test_db_fixture_is_a_migrated_database(db: sqlite3.Connection) -> None:
    assert _version(db) == len(meals.db.MIGRATIONS)
    assert set(PLAN_SCHEMA) <= _tables(db)


# ── migrations ───────────────────────────────────────────────────────────────


def test_reopening_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        _insert(conn, "pantry_item", name="tahini")
        conn.commit()

    with _open(tmp_path) as reopened:
        assert _version(reopened) == len(meals.db.MIGRATIONS)
        assert meals.db.migrate(reopened) == len(meals.db.MIGRATIONS)
        assert _column(reopened, "SELECT name FROM pantry_item") == ["tahini"]


def test_migration_2_upgrades_a_v1_database_and_keeps_its_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = meals.db.MIGRATIONS
    monkeypatch.setattr(meals.db, "MIGRATIONS", migrations[:1])
    with _open(tmp_path) as v1:
        _insert(v1, "pantry_item", aliases='["evoo"]', typical_interval_days=70)
        (item_id,) = _column(v1, "SELECT id FROM pantry_item")
        v1.execute(
            "INSERT INTO purchase_log (item_id, purchased_on, qty, price_cents) VALUES (?, ?, ?, ?)",
            (item_id, "2026-07-23", 1.0, 899),
        )
        v1.commit()

    monkeypatch.setattr(meals.db, "MIGRATIONS", migrations[:2])
    with _open(tmp_path) as conn:
        assert _version(conn) == 2
        items = conn.execute(
            "SELECT id, name, aliases, category, typical_interval_days, next_ask_on FROM pantry_item"
        )
        assert [tuple(row) for row in items] == [
            (item_id, "olive oil", '["evoo"]', "staple", 70, None)
        ]
        purchases = conn.execute("SELECT item_id, purchased_on, qty, price_cents FROM purchase_log")
        assert [tuple(row) for row in purchases] == [(item_id, "2026-07-23", 1.0, 899)]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(LOG_PURCHASE, (item_id, "2026-07-23"))


def test_migration_2_refuses_duplicate_purchases_and_leaves_v1_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seam map: non-destructive, so it fails atomically rather than deleting purchase history."""
    path = tmp_path / "pantry.sqlite"
    migrations = meals.db.MIGRATIONS
    monkeypatch.setattr(meals.db, "MIGRATIONS", migrations[:1])
    with _open(tmp_path) as v1:
        _insert(v1, "pantry_item")
        (item_id,) = _column(v1, "SELECT id FROM pantry_item")
        v1.executemany(LOG_PURCHASE, [(item_id, "2026-09-20")] * 2)
        v1.commit()

    monkeypatch.setattr(meals.db, "MIGRATIONS", migrations[:2])
    with pytest.raises(sqlite3.IntegrityError) as caught:
        meals.db.get_db(path).close()
    # A note names the failing migration (2) and the version the database stays at (v1).
    notes = getattr(caught.value, "__notes__", [])
    assert any(
        re.search(r"(?<!\d)2(?!\d)", note) and re.search(r"\bv1\b", note) for note in notes
    ), notes
    with closing(sqlite3.connect(path)) as check:
        assert _version(check) == 1
        assert "next_ask_on" not in _column(
            check, "SELECT name FROM pragma_table_info('pantry_item')"
        )
        assert check.execute("SELECT count(*) FROM purchase_log").fetchone()[0] == 2


def test_appended_migration_is_applied_once_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meals.db.get_db(tmp_path / "pantry.sqlite").close()
    latest = len(meals.db.MIGRATIONS)
    monkeypatch.setattr(meals.db, "MIGRATIONS", (*meals.db.MIGRATIONS, PROBE))

    with _open(tmp_path) as conn:
        assert _version(conn) == latest + 1
        assert meals.db.migrate(conn) == latest + 1
        assert _column(conn, "SELECT x FROM probe") == [42]


def test_migrating_a_fresh_file_logs_the_from_and_to_versions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="meals.db")
    latest = len(meals.db.MIGRATIONS)

    meals.db.get_db(tmp_path / "pantry.sqlite").close()

    versions = re.compile(rf"(?<!\d)0(?!\d).*(?<!\d){latest}(?!\d)")
    infos = [
        r.getMessage() for r in caplog.records if r.name == "meals.db" and r.levelno == logging.INFO
    ]
    assert any(versions.search(message) for message in infos), infos


def test_database_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    """Old code must not write to a schema it doesn't know."""
    meals.db.get_db(tmp_path / "pantry.sqlite").close()
    with closing(sqlite3.connect(tmp_path / "pantry.sqlite")) as raw:
        raw.execute("PRAGMA user_version = 99")

    with pytest.raises(Exception, match=r"(?i)newer"):  # the type is unspecified
        meals.db.get_db(tmp_path / "pantry.sqlite").close()


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


def test_migrate_refuses_to_commit_the_callers_open_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending migration must not commit the caller's half-done writes as a side effect."""
    meals.db.get_db(tmp_path / "pantry.sqlite").close()
    latest = len(meals.db.MIGRATIONS)
    monkeypatch.setattr(meals.db, "MIGRATIONS", (*meals.db.MIGRATIONS, PROBE))

    with closing(sqlite3.connect(tmp_path / "pantry.sqlite")) as conn:
        _insert(conn, "pantry_item", name="tahini")
        assert conn.in_transaction
        try:
            meals.db.migrate(conn)
        except Exception:  # the type is unspecified; what matters is the caller's transaction
            pass
        else:
            pytest.fail("migrate() ran a pending migration inside the caller's transaction")

        conn.rollback()
        assert _column(conn, "SELECT name FROM pantry_item") == []
        assert _version(conn) == latest
        assert "probe" not in _tables(conn)


def test_migration_that_ends_its_own_transaction_surfaces_the_real_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing statement's error, not "cannot rollback - no transaction is active"."""
    meals.db.get_db(tmp_path / "pantry.sqlite").close()
    latest = len(meals.db.MIGRATIONS)
    broken = ("CREATE TABLE probe (x INTEGER)", "COMMIT", "THIS IS NOT SQL")
    monkeypatch.setattr(meals.db, "MIGRATIONS", (*meals.db.MIGRATIONS, broken))

    with pytest.raises(sqlite3.OperationalError, match="syntax error"):
        meals.db.get_db(tmp_path / "pantry.sqlite").close()
    with closing(sqlite3.connect(tmp_path / "pantry.sqlite")) as check:
        assert _version(check) == latest


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
