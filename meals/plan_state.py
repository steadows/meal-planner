"""The weekly plan's persisted state: the only reader and writer of `weekly_plan` and `job_run`
(ADR-0001, State and Job contract).

Every function takes the caller's connection and runs each write as its own transaction. A
compare-and-set returns whether it matched; any other write must change exactly one row, or it
rolls back and raises PlanStateError, so a write that silently matched nothing can't pass as done.
Timestamps are written by SQLite's CURRENT_TIMESTAMP only, UTC text 'YYYY-MM-DD HH:MM:SS', and read
back as UTC-aware datetimes.

Stored proposals are reloaded with `context={TRUSTED: True}`, the one place TRUSTED is passed: the
planner takes `mealie_slug` only from Mealie, never from Claude's text (ADR-0001 :68-70).
"""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import field_validator

from meals.contracts import TRUSTED, CartReport, Contract, Custody, WeekProposal

Status = Literal["proposed", "approved", "cart_filled", "ordered"]
Outcome = Literal["done", "failed", "interrupted"]
Claim = Literal["claimed", "finished", "running", "interrupted"]

_SQLITE_TIME = "%Y-%m-%d %H:%M:%S"
# job_run is read by named columns: a later (append-only) migration may add one, and JobRun
# forbids unknown fields.
_RUN_COLUMNS = "job, week_start, started_at, finished_at, outcome, detail"
APPROVED_OR_LATER: tuple[Status, ...] = ("approved", "cart_filled", "ordered")


class PlanStateError(RuntimeError):
    """A write that must change exactly one row matched none (or several)."""


def _utc(value: Any) -> Any:
    """SQLite's CURRENT_TIMESTAMP text, which is UTC, as an aware datetime."""
    if isinstance(value, str):
        return datetime.strptime(value, _SQLITE_TIME).replace(tzinfo=UTC)
    return value


class JobRun(Contract):
    """One `job_run` row: a job's claim on a week."""

    job: str
    week_start: date
    started_at: datetime
    finished_at: datetime | None = None
    outcome: Outcome | None = None
    detail: str | None = None

    _as_utc = field_validator("started_at", "finished_at", mode="before")(_utc)


class StoredWeek(Contract):
    """One `weekly_plan` row, with `components` reloaded as the WeekProposal it holds."""

    week_start: date
    status: Status
    custody: Custody | None
    proposal: WeekProposal
    mealie_plan_ref: str | None = None
    approved_at: datetime | None = None

    _as_utc = field_validator("approved_at", mode="before")(_utc)


# ── reads ────────────────────────────────────────────────────────────────────


def _marks(values: Sequence[object]) -> str:
    """One `?` per value, for an `IN (...)` list."""
    return ", ".join("?" * len(values))


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[object]) -> list[sqlite3.Row]:
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row  # whatever the caller's connection uses
    return cursor.execute(sql, params).fetchall()


def _week(row: sqlite3.Row) -> StoredWeek:
    return StoredWeek(
        week_start=row["week_start"],
        status=row["status"],
        custody=row["custody"],
        proposal=WeekProposal.model_validate_json(row["components"], context={TRUSTED: True}),
        mealie_plan_ref=row["mealie_plan_ref"],
        approved_at=row["approved_at"],
    )


def _run(row: sqlite3.Row) -> JobRun:
    return JobRun.model_validate(dict(row))


def get_week(conn: sqlite3.Connection, week_start: date) -> StoredWeek | None:
    rows = _rows(conn, "SELECT * FROM weekly_plan WHERE week_start = ?", (week_start.isoformat(),))
    return _week(rows[0]) if rows else None


def recent_proposals(
    conn: sqlite3.Connection, before: date, limit: int = 3
) -> tuple[WeekProposal, ...]:
    """Approved-or-later weeks before `before`, newest first: the planner's `recent` weeks."""
    rows = _rows(
        conn,
        f"SELECT * FROM weekly_plan WHERE status IN ({_marks(APPROVED_OR_LATER)})"
        " AND week_start < ?"
        " ORDER BY week_start DESC LIMIT ?",
        (*APPROVED_OR_LATER, before.isoformat(), limit),
    )
    return tuple(_week(row).proposal for row in rows)


def latest_week(conn: sqlite3.Connection, before: date) -> StoredWeek | None:
    """The newest week before `before`, in any status."""
    rows = _rows(
        conn,
        "SELECT * FROM weekly_plan WHERE week_start < ? ORDER BY week_start DESC LIMIT 1",
        (before.isoformat(),),
    )
    return _week(rows[0]) if rows else None


def week_starts_since(conn: sqlite3.Connection, since: date) -> tuple[date, ...]:
    """Every stored week from `since` on, oldest first, in any status. The caller reads each with
    `get_week`, so one unreadable row can't hide the others."""
    rows = _rows(
        conn,
        "SELECT week_start FROM weekly_plan WHERE week_start >= ? ORDER BY week_start",
        (since.isoformat(),),
    )
    return tuple(date.fromisoformat(row["week_start"]) for row in rows)


def get_run(conn: sqlite3.Connection, job: str, week_start: date) -> JobRun | None:
    rows = _rows(
        conn,
        f"SELECT {_RUN_COLUMNS} FROM job_run WHERE job = ? AND week_start = ?",
        (job, week_start.isoformat()),
    )
    return _run(rows[0]) if rows else None


def running(conn: sqlite3.Connection, job: str) -> JobRun | None:
    """The oldest unfinished claim for `job`, in any week: the lock holder's may not be ours."""
    rows = _rows(
        conn,
        f"SELECT {_RUN_COLUMNS} FROM job_run WHERE job = ? AND outcome IS NULL"
        " ORDER BY started_at, week_start LIMIT 1",
        (job,),
    )
    return _run(rows[0]) if rows else None


def latest_run(conn: sqlite3.Connection, job: str, outcomes: Sequence[Outcome]) -> JobRun | None:
    """The newest week whose `job` claim ended with one of `outcomes` ("retry cart")."""
    rows = _rows(
        conn,
        f"SELECT {_RUN_COLUMNS} FROM job_run WHERE job = ? AND outcome IN ({_marks(outcomes)})"
        " ORDER BY week_start DESC LIMIT 1",
        (job, *outcomes),
    )
    return _run(rows[0]) if rows else None


# ── weekly_plan writes ───────────────────────────────────────────────────────


def insert_week(conn: sqlite3.Connection, proposal: WeekProposal, status: Status) -> bool:
    """Insert the week, or return False if it already exists (the caller re-reads)."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO weekly_plan (week_start, custody, components, status, approved_at)"
            " VALUES (?, ?, ?, ?, CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP END)"
            " ON CONFLICT(week_start) DO NOTHING",
            (
                proposal.week_start.isoformat(),
                proposal.custody,
                proposal.model_dump_json(),
                status,
                status,
            ),
        )
    return cursor.rowcount == 1


def transition(
    conn: sqlite3.Connection,
    week_start: date,
    from_status: Status,
    to_status: Status,
    *,
    proposal: WeekProposal | None = None,
) -> bool:
    """Compare-and-set the week's status, optionally replacing its stored proposal. Moving to
    `approved` stamps `approved_at`."""
    components = proposal.model_dump_json() if proposal is not None else None
    custody = proposal.custody if proposal is not None else None
    with conn:
        cursor = conn.execute(
            "UPDATE weekly_plan SET status = ?, components = COALESCE(?, components),"
            " custody = COALESCE(?, custody),"
            " approved_at = CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE approved_at END"
            " WHERE week_start = ? AND status = ?",
            (to_status, components, custody, to_status, week_start.isoformat(), from_status),
        )
    return cursor.rowcount == 1


def set_mealie_ref(conn: sqlite3.Connection, week_start: date, ref: str) -> bool:
    """Record the published Mealie plan, once, on an approved-or-later week."""
    with conn:
        cursor = conn.execute(
            "UPDATE weekly_plan SET mealie_plan_ref = ?"
            " WHERE week_start = ? AND mealie_plan_ref IS NULL"
            f" AND status IN ({_marks(APPROVED_OR_LATER)})",
            (ref, week_start.isoformat(), *APPROVED_OR_LATER),
        )
    return cursor.rowcount == 1


def record_cart(conn: sqlite3.Connection, week_start: date, report: CartReport) -> None:
    """In one transaction: the week goes approved → cart_filled, and the unfinished cart_fill
    claim's `detail` gets the report. Either write missing rolls both back."""
    week = week_start.isoformat()
    with conn:
        filled = conn.execute(
            "UPDATE weekly_plan SET status = 'cart_filled' WHERE week_start = ? AND status = 'approved'",
            (week,),
        ).rowcount
        recorded = conn.execute(
            "UPDATE job_run SET detail = ?"
            " WHERE job = 'cart_fill' AND week_start = ? AND outcome IS NULL",
            (report.model_dump_json(), week),
        ).rowcount
        if (filled, recorded) != (1, 1):
            raise PlanStateError(
                f"can't record the cart for week {week}: it needs an approved week and an "
                f"unfinished cart_fill claim (matched {filled} week(s), {recorded} claim(s))"
            )


# ── job_run writes ───────────────────────────────────────────────────────────


def _write_one(conn: sqlite3.Connection, sql: str, params: Sequence[object], failure: str) -> None:
    with conn:
        rowcount = conn.execute(sql, params).rowcount
        if rowcount != 1:
            raise PlanStateError(f"{failure} (matched {rowcount} rows)")


def claim(conn: sqlite3.Connection, job: str, week_start: date) -> Claim:
    """Claim `job` for the week. Anything but 'claimed' is an existing claim's state."""
    week = week_start.isoformat()
    with conn:
        cursor = conn.execute(
            "INSERT INTO job_run (job, week_start) VALUES (?, ?)"
            " ON CONFLICT(job, week_start) DO NOTHING",
            (job, week),
        )
    if cursor.rowcount == 1:
        return "claimed"
    existing = get_run(conn, job, week_start)
    if existing is None:
        raise PlanStateError(f"claim {job} for week {week} neither inserted nor found a row")
    if existing.outcome is None:
        return "running"
    return "interrupted" if existing.outcome == "interrupted" else "finished"


def set_detail(conn: sqlite3.Connection, job: str, week_start: date, reason: str) -> None:
    """Write a failure reason on an unfinished claim (the catch-all, before its message)."""
    week = week_start.isoformat()
    _write_one(
        conn,
        "UPDATE job_run SET detail = ? WHERE job = ? AND week_start = ? AND outcome IS NULL",
        (reason, job, week),
        f"no unfinished {job} claim for week {week} to write a reason on",
    )


def finish(conn: sqlite3.Connection, job: str, week_start: date, outcome: Outcome) -> None:
    """Set the outcome and finished_at together. Never touches `detail`."""
    week = week_start.isoformat()
    _write_one(
        conn,
        "UPDATE job_run SET outcome = ?, finished_at = CURRENT_TIMESTAMP"
        " WHERE job = ? AND week_start = ? AND outcome IS NULL",
        (outcome, job, week),
        f"no unfinished {job} claim for week {week} to finish as {outcome}",
    )


def reset_for_retry(conn: sqlite3.Connection, job: str, week_start: date) -> JobRun:
    """Reopen an interrupted or failed claim for `--retry`; return the prior row for the log."""
    week = week_start.isoformat()
    prior = get_run(conn, job, week_start)
    if prior is None:
        raise PlanStateError(f"no {job} claim for week {week} to retry")
    _write_one(
        conn,
        "UPDATE job_run SET started_at = CURRENT_TIMESTAMP, finished_at = NULL, outcome = NULL,"
        " detail = NULL WHERE job = ? AND week_start = ? AND outcome IN ('interrupted', 'failed')",
        (job, week),
        f"no interrupted or failed {job} claim for week {week} to retry",
    )
    return prior
