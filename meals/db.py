"""SQLite connection and schema migrations (PLAN.md, Table schema). Only the contracts lane edits this file."""

import sqlite3
import time
from pathlib import Path

from meals.config import get_settings

BUSY_TIMEOUT_S = 5.0
_WAL_RETRY_SLEEP_S = 0.05

# Append-only. Migration N (1-based) is MIGRATIONS[N - 1], one SQL statement per string.
# PRAGMA user_version records how many have been applied.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE pantry_item (
            id                     INTEGER PRIMARY KEY,
            name                   TEXT NOT NULL UNIQUE,
            aliases                TEXT,
            category               TEXT NOT NULL CHECK (category IN ('staple','perishable','fallback')),
            status                 TEXT NOT NULL DEFAULT 'have' CHECK (status IN ('have','buy_next_time')),
            typical_interval_days  INTEGER,
            last_purchased         DATE,
            default_qty            REAL,
            default_unit           TEXT,
            meijer_product_id      TEXT,
            meijer_url             TEXT,
            preferred_product_name TEXT,
            substitute_ok          INTEGER NOT NULL DEFAULT 1,
            for_miles              INTEGER NOT NULL DEFAULT 0,
            notes                  TEXT,
            updated_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE purchase_log (
            id            INTEGER PRIMARY KEY,
            item_id       INTEGER NOT NULL REFERENCES pantry_item(id),
            purchased_on  DATE NOT NULL,
            qty           REAL,
            price_cents   INTEGER,
            source        TEXT DEFAULT 'meijer_pickup'
        )""",
        """CREATE TABLE weekly_plan (
            id              INTEGER PRIMARY KEY,
            week_start      DATE NOT NULL UNIQUE,
            custody         TEXT,
            components      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed','approved','cart_filled','ordered')),
            mealie_plan_ref TEXT,
            approved_at     DATETIME,
            notes           TEXT
        )""",
        """CREATE TABLE meal_rating (
            id          INTEGER PRIMARY KEY,
            week_start  DATE NOT NULL,
            component   TEXT NOT NULL,
            rater       TEXT NOT NULL CHECK (rater IN ('steve','miles')),
            rating      INTEGER NOT NULL CHECK (rating IN (-1, 1)),
            rated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ),
)


def _version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations atomically and return the resulting schema version.

    An up-to-date database returns without taking a lock. Otherwise it takes BEGIN IMMEDIATE and
    re-reads user_version inside the lock, so two processes starting at once apply each migration
    exactly once; any open transaction on `conn` is committed first. A failing migration rolls
    back entirely.
    """
    version = _version(conn)
    if version >= len(MIGRATIONS):
        return version
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # we issue BEGIN/COMMIT ourselves; never executescript()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = _version(conn)  # re-read under the lock: another process may have won
            for statements in MIGRATIONS[version:]:
                for statement in statements:
                    conn.execute(statement)
            if version < len(MIGRATIONS):
                conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return _version(conn)
    finally:
        conn.isolation_level = previous_isolation


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL, retrying: on a fresh file SQLite can report "database is locked" here
    immediately, without honouring the busy timeout, while another opener holds a lock."""
    deadline = time.monotonic() + BUSY_TIMEOUT_S
    while True:
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() == "wal":
                return
            failure = f"journal_mode stayed {mode!r}"
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc):
                raise
            failure = str(exc)
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError(f"could not enable WAL: {failure}")
        time.sleep(_WAL_RETRY_SLEEP_S)


def get_db(path: Path | None = None) -> sqlite3.Connection:
    """Open the pantry database (default: settings.pantry_db) and bring its schema up to date.

    Creates the parent directory, sets row_factory=sqlite3.Row, foreign_keys=ON, journal_mode=WAL
    and a busy timeout, then runs migrate().
    """
    target = path if path is not None else get_settings().pantry_db
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_S)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _enable_wal(conn)
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn
