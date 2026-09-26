"""meals.plan_state: the only reader and writer of `weekly_plan` and `job_run`.

Authority: the P2 seam map (.context/seams/P2.md, `meals/plan_state.py`: the symbol table, "Rowcounts
are asserted on every write", "Timestamps ... SQL CURRENT_TIMESTAMP only") and ADR-0001 (State :62-74,
Job contract :136-150, Retries :177-180, `job_run` DDL :304-321, Integration Test Points :419-420).

Every test runs on the real migrated schema (the `db` fixture). Setup writes rows straight to the
tables, and every check reads back through a second connection (`reader`), so a write counts only
once it is committed, and nothing is trusted from the call's own return value.
"""

import multiprocessing
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from meals.contracts import (
    TRUSTED,
    CartReport,
    Components,
    Contract,
    Custody,
    RecipeOption,
    Substitution,
    WeekProposal,
)
from meals.db import get_db

_MISSING: ImportError | None = None
try:
    from meals.plan_state import JobRun, PlanStateError, StoredWeek

    from meals import plan_state
except ImportError as exc:  # RED: meals/plan_state.py isn't written yet
    _MISSING = exc

W = date(2026, 9, 27)  # the Sunday after conftest's TODAY
PRIOR = date(2026, 9, 20)
SEED_START = "2026-09-26 12:00:00"
OLD_START = "2026-01-04 09:30:00"  # a started_at that a reset must not leave in place
OLD_FINISH = "2026-01-04 09:45:00"
SQLITE_UTC = re.compile(r"\A\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\Z")
FAJITAS = "sheet-pan-chicken-fajitas"
RACE_WAIT_S = 20.0
Outcome = Literal["done", "failed", "interrupted"]  # the seam map's Outcome
REPORT = CartReport(
    added=("chicken thighs (2 lb)", "corn tortillas"),
    substituted=(Substitution(wanted="cilantro", used="flat-leaf parsley"),),
    missing=("tahini",),
    subtotal_cents=4217,
)


@pytest.fixture(autouse=True)
def _plan_state_exists() -> None:
    if _MISSING is not None:
        pytest.fail(
            f"meals.plan_state is not built yet (P2 seam map, `meals/plan_state.py`): {_MISSING}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _system_clock_is_not_detroit() -> Iterator[None]:
    """Timestamps are UTC text; a parser or writer that leans on the process's local zone shows up."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.fixture
def reader(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A second connection: it sees only what plan_state committed."""
    conn = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()["file"])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _option(name: str, slug: str | None = None) -> RecipeOption:
    return RecipeOption.model_validate(
        {
            "name": name,
            "url": "https://example.com/" + name.lower().replace(" ", "-"),
            "source": "example.com",
            "hands_on_min": 20,
            "servings": 4,
            "batch_ok": True,
            "fit_note": "Miles-friendly",
            "ingredients": [],
            "steps": ["Cook it."],
            "mealie_slug": slug,
        },
        context={TRUSTED: True},
    )


def _proposal(
    week: date,
    pick: str = "Sheet-pan chicken fajitas",
    *,
    slug: str | None = None,
    custody: Custody = "wed+sat_sun",
) -> WeekProposal:
    return WeekProposal(
        week_start=week,
        custody=custody,
        recipe_options=(_option(pick, slug),),
        components=Components(proteins=("chicken thighs",), grains=("brown rice",)),
        lunch_builds=("Chicken sweet-potato bowl",),
        kid_nights=("Wed cook-with-Miles (ravioli)",),
        pantry_questions=(),
    )


def _seed_week(
    db: sqlite3.Connection, proposal: WeekProposal, status: str, *, ref: str | None = None
) -> None:
    approved_at = None if status == "proposed" else SEED_START
    db.execute(
        "INSERT INTO weekly_plan (week_start, custody, components, status, mealie_plan_ref,"
        " approved_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            proposal.week_start.isoformat(),
            proposal.custody,
            proposal.model_dump_json(),
            status,
            ref,
            approved_at,
        ),
    )
    db.commit()


def _seed_claim(
    db: sqlite3.Connection,
    job: str,
    week: date,
    *,
    outcome: str | None = None,
    detail: str | None = None,
    started: str = SEED_START,
    finished: str | None = None,
) -> None:
    if outcome is not None and finished is None:
        finished = started
    db.execute(
        "INSERT INTO job_run (job, week_start, started_at, finished_at, outcome, detail)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (job, week.isoformat(), started, finished, outcome, detail),
    )
    db.commit()


def _week_row(reader: sqlite3.Connection, week: date) -> sqlite3.Row | None:
    row: sqlite3.Row | None = reader.execute(
        "SELECT * FROM weekly_plan WHERE week_start = ?", (week.isoformat(),)
    ).fetchone()
    return row


def _claim_row(reader: sqlite3.Connection, job: str, week: date) -> sqlite3.Row | None:
    row: sqlite3.Row | None = reader.execute(
        "SELECT * FROM job_run WHERE job = ? AND week_start = ?", (job, week.isoformat())
    ).fetchone()
    return row


def _snapshot(reader: sqlite3.Connection) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    runs = reader.execute("SELECT * FROM job_run ORDER BY job, week_start").fetchall()
    weeks = reader.execute("SELECT * FROM weekly_plan ORDER BY week_start").fetchall()
    return [tuple(r) for r in runs], [tuple(r) for r in weeks]


def _stored(row: sqlite3.Row) -> WeekProposal:
    return WeekProposal.model_validate_json(row["components"], context={TRUSTED: True})


def _written_just_now(text: object) -> bool:
    """SQLite's CURRENT_TIMESTAMP form, in UTC, within a few minutes of the real clock."""
    assert isinstance(text, str) and SQLITE_UTC.match(text), f"not UTC text from SQLite: {text!r}"
    written = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return abs(datetime.now(UTC) - written) < timedelta(minutes=5)


# ── claim ────────────────────────────────────────────────────────────────────


def test_claim_inserts_an_unfinished_row(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    assert plan_state.claim(db, "cart_fill", W) == "claimed"

    row = _claim_row(reader, "cart_fill", W)
    assert row is not None
    assert (row["outcome"], row["finished_at"], row["detail"]) == (None, None, None)
    assert _written_just_now(row["started_at"])


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (None, "running"),
        ("interrupted", "interrupted"),
        ("done", "finished"),
        ("failed", "finished"),
    ],
)
def test_second_claim_reports_the_existing_row_and_changes_nothing(
    db: sqlite3.Connection, reader: sqlite3.Connection, outcome: str | None, expected: str
) -> None:
    # Seam map, `claim`: on a conflict it re-reads (ADR :320's Claim literal).
    _seed_claim(db, "cart_fill", W, outcome=outcome, detail="earlier reason")
    before = _snapshot(reader)

    assert plan_state.claim(db, "cart_fill", W) == expected
    assert _snapshot(reader) == before


def test_claims_are_one_per_job_and_week(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    # `job_run` PRIMARY KEY (job, week_start), ADR :314.
    assert plan_state.claim(db, "cart_fill", W) == "claimed"
    assert plan_state.claim(db, "sat_propose", W) == "claimed"
    assert plan_state.claim(db, "cart_fill", PRIOR) == "claimed"

    assert len(_snapshot(reader)[0]) == 3


def _claim_in_child(path: str, go: Event, results: "Queue[str]") -> None:
    conn = get_db(Path(path))
    try:
        go.wait(RACE_WAIT_S)
        results.put(str(plan_state.claim(conn, "cart_fill", W)))
    except Exception as exc:  # reported to the parent, which fails the test with it
        results.put(f"raised {exc!r}")
    finally:
        conn.close()


def test_two_processes_racing_one_claim_get_exactly_one_claimed(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    # ADR Integration Test Points :419-420: "two processes racing a claim".
    path = db.execute("PRAGMA database_list").fetchone()["file"]
    context = multiprocessing.get_context("spawn")
    go = context.Event()
    results: Queue[str] = context.Queue()
    children = [context.Process(target=_claim_in_child, args=(path, go, results)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        go.set()
        outcomes = sorted(results.get(timeout=RACE_WAIT_S) for _ in children)
    finally:
        for child in children:
            child.join(RACE_WAIT_S)
            if child.is_alive():
                child.terminate()

    assert outcomes == ["claimed", "running"]
    row = _claim_row(reader, "cart_fill", W)
    assert row is not None and row["outcome"] is None


# ── finish, set_detail ───────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["done", "failed", "interrupted"])
def test_finish_sets_outcome_and_finished_at_and_never_touches_detail(
    db: sqlite3.Connection, reader: sqlite3.Connection, outcome: Outcome
) -> None:
    _seed_claim(db, "cart_fill", W, detail="the committed result")

    plan_state.finish(db, "cart_fill", W, outcome)

    row = _claim_row(reader, "cart_fill", W)
    assert row is not None
    assert row["outcome"] == outcome
    assert _written_just_now(row["finished_at"])
    assert row["detail"] == "the committed result"
    assert row["started_at"] == SEED_START


@pytest.mark.parametrize("state", ["done", "failed", "interrupted", "missing"])
def test_finish_on_a_claim_that_is_not_running_raises_and_writes_nothing(
    db: sqlite3.Connection, reader: sqlite3.Connection, state: str
) -> None:
    # Seam map: any non-CAS write expects rowcount 1, else rolls back and raises PlanStateError
    # naming the job and the week (the silent-write guard).
    if state != "missing":
        _seed_claim(db, "cart_fill", W, outcome=state, detail="first result")
    before = _snapshot(reader)

    with pytest.raises(PlanStateError) as raised:
        plan_state.finish(db, "cart_fill", W, "done")

    assert "cart_fill" in str(raised.value) and W.isoformat() in str(raised.value)
    assert _snapshot(reader) == before
    assert not db.in_transaction


def test_set_detail_writes_the_reason_on_an_unfinished_claim(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    _seed_claim(db, "cart_fill", W)

    plan_state.set_detail(db, "cart_fill", W, "Meijer asked for a login")

    row = _claim_row(reader, "cart_fill", W)
    assert row is not None
    assert (row["detail"], row["outcome"], row["finished_at"]) == (
        "Meijer asked for a login",
        None,
        None,
    )


@pytest.mark.parametrize("state", ["done", "failed", "interrupted", "missing"])
def test_set_detail_on_a_finished_or_missing_claim_raises_and_keeps_the_result(
    db: sqlite3.Connection, reader: sqlite3.Connection, state: str
) -> None:
    # ADR :161-164, result preservation: nothing rewrites a committed detail.
    if state != "missing":
        _seed_claim(db, "cart_fill", W, outcome=state, detail=REPORT.model_dump_json())
    before = _snapshot(reader)

    with pytest.raises(PlanStateError):
        plan_state.set_detail(db, "cart_fill", W, "a later diagnostic")

    assert _snapshot(reader) == before
    assert not db.in_transaction


# ── reset_for_retry ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["interrupted", "failed"])
def test_reset_for_retry_returns_the_prior_row_and_reopens_the_claim(
    db: sqlite3.Connection, reader: sqlite3.Connection, outcome: str
) -> None:
    # Seam map `reset_for_retry`; ADR Retries :177-180.
    _seed_claim(
        db,
        "cart_fill",
        W,
        outcome=outcome,
        detail="expired",
        started=OLD_START,
        finished=OLD_FINISH,
    )

    prior = plan_state.reset_for_retry(db, "cart_fill", W)

    assert isinstance(prior, JobRun)
    assert (prior.job, prior.week_start, prior.outcome, prior.detail) == (
        "cart_fill",
        W,
        outcome,
        "expired",
    )
    assert prior.started_at == datetime(2026, 1, 4, 9, 30, tzinfo=UTC)
    row = _claim_row(reader, "cart_fill", W)
    assert row is not None
    assert (row["outcome"], row["finished_at"], row["detail"]) == (None, None, None)
    assert _written_just_now(row["started_at"])


@pytest.mark.parametrize("state", ["unfinished", "done", "missing"])
def test_reset_for_retry_refuses_a_claim_that_is_not_interrupted_or_failed(
    db: sqlite3.Connection, reader: sqlite3.Connection, state: str
) -> None:
    if state != "missing":
        outcome = None if state == "unfinished" else state
        _seed_claim(db, "cart_fill", W, outcome=outcome, detail="a result", started=OLD_START)
    before = _snapshot(reader)

    with pytest.raises(PlanStateError):
        plan_state.reset_for_retry(db, "cart_fill", W)

    assert _snapshot(reader) == before
    assert not db.in_transaction


# ── weekly_plan writes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["proposed", "approved"])
def test_insert_week_stores_the_proposal(
    db: sqlite3.Connection, reader: sqlite3.Connection, status: Literal["proposed", "approved"]
) -> None:
    proposal = _proposal(W, slug=FAJITAS, custody="wed+fri_sat")

    assert plan_state.insert_week(db, proposal, status) is True

    row = _week_row(reader, W)
    assert row is not None
    assert (row["status"], row["custody"], row["mealie_plan_ref"]) == (status, "wed+fri_sat", None)
    assert _stored(row) == proposal
    if status == "approved":
        assert _written_just_now(row["approved_at"])
    else:
        assert row["approved_at"] is None


def test_insert_week_on_a_conflict_is_false_and_leaves_the_row(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    # ADR :143, Risk #7: ON CONFLICT(week_start) DO NOTHING, and the caller re-reads.
    _seed_week(db, _proposal(W, "First plan"), "approved", ref="plan-1")
    before = _snapshot(reader)

    assert plan_state.insert_week(db, _proposal(W, "Second plan"), "proposed") is False
    assert _snapshot(reader) == before


def test_transition_to_approved_sets_approved_at_and_keeps_components(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    proposal = _proposal(W)
    _seed_week(db, proposal, "proposed")

    assert plan_state.transition(db, W, "proposed", "approved") is True

    row = _week_row(reader, W)
    assert row is not None
    assert row["status"] == "approved"
    assert _written_just_now(row["approved_at"])
    assert _stored(row) == proposal


def test_transition_with_a_proposal_replaces_components(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    # Seam map `transition`: approval stores the narrowed plan, autoapprove the restamped one.
    _seed_week(db, _proposal(W, "The full proposal"), "proposed")
    narrowed = _proposal(W, "Steve's pick", slug=FAJITAS)

    assert plan_state.transition(db, W, "proposed", "approved", proposal=narrowed) is True

    row = _week_row(reader, W)
    assert row is not None
    assert _stored(row) == narrowed


def test_transition_past_approved_keeps_approved_at(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    _seed_week(db, _proposal(W), "approved")

    assert plan_state.transition(db, W, "approved", "cart_filled") is True

    row = _week_row(reader, W)
    assert row is not None
    assert (row["status"], row["approved_at"]) == ("cart_filled", SEED_START)


@pytest.mark.parametrize("seeded", ["approved", "missing"])
def test_transition_from_another_status_is_false_and_writes_nothing(
    db: sqlite3.Connection, reader: sqlite3.Connection, seeded: str
) -> None:
    if seeded != "missing":
        _seed_week(db, _proposal(W), seeded)
    before = _snapshot(reader)

    assert (
        plan_state.transition(db, W, "proposed", "approved", proposal=_proposal(W, "Other"))
        is False
    )
    assert _snapshot(reader) == before


@pytest.mark.parametrize("status", ["approved", "cart_filled", "ordered"])
def test_set_mealie_ref_sets_a_null_ref_exactly_once(
    db: sqlite3.Connection, reader: sqlite3.Connection, status: str
) -> None:
    _seed_week(db, _proposal(W), status)

    assert plan_state.set_mealie_ref(db, W, "plan-1") is True
    assert plan_state.set_mealie_ref(db, W, "plan-2") is False

    row = _week_row(reader, W)
    assert row is not None
    assert (row["mealie_plan_ref"], row["status"]) == ("plan-1", status)


def test_set_mealie_ref_refuses_a_proposed_or_missing_week(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    # Seam map: the status must be approved or later (ADR :75-77: published after approval).
    _seed_week(db, _proposal(W), "proposed")

    assert plan_state.set_mealie_ref(db, W, "plan-1") is False
    assert plan_state.set_mealie_ref(db, PRIOR, "plan-1") is False

    row = _week_row(reader, W)
    assert row is not None and row["mealie_plan_ref"] is None
    assert _week_row(reader, PRIOR) is None


# ── record_cart ──────────────────────────────────────────────────────────────


def test_record_cart_fills_the_week_and_stores_the_report_in_one_commit(
    db: sqlite3.Connection, reader: sqlite3.Connection
) -> None:
    _seed_week(db, _proposal(W), "approved")
    _seed_claim(db, "cart_fill", W)

    plan_state.record_cart(db, W, REPORT)

    week = _week_row(reader, W)
    run = _claim_row(reader, "cart_fill", W)
    assert week is not None and run is not None
    assert week["status"] == "cart_filled"
    assert CartReport.model_validate_json(run["detail"]) == REPORT
    assert (run["outcome"], run["finished_at"]) == (None, None)  # recording isn't finishing


@pytest.mark.parametrize(
    ("status", "claim"),
    [
        pytest.param("proposed", "unfinished", id="week-not-approved"),
        pytest.param("cart_filled", "unfinished", id="week-already-filled"),
        pytest.param("approved", "done", id="claim-finished"),
        pytest.param("approved", "failed", id="claim-failed"),
        pytest.param("approved", "missing", id="no-claim"),
    ],
)
def test_record_cart_rolls_back_both_tables_when_either_write_misses(
    db: sqlite3.Connection, reader: sqlite3.Connection, status: str, claim: str
) -> None:
    # Seam map `record_cart`: either write missing → roll back both, then PlanStateError.
    _seed_week(db, _proposal(W), status)
    if claim != "missing":
        _seed_claim(db, "cart_fill", W, outcome=None if claim == "unfinished" else claim)
    before = _snapshot(reader)

    with pytest.raises(PlanStateError):
        plan_state.record_cart(db, W, REPORT)

    assert not db.in_transaction
    assert _snapshot(reader) == before


# ── reads ────────────────────────────────────────────────────────────────────


def test_get_week_reloads_a_stored_mealie_slug_as_trusted_data(
    db: sqlite3.Connection,
) -> None:
    # ADR :68-70 and the seam map: plan_state is the one TRUSTED reload site.
    proposal = _proposal(W, slug=FAJITAS, custody="wed+fri_sat")
    with pytest.raises(ValidationError):  # the trap is live: an untrusted reload refuses the slug
        WeekProposal.model_validate_json(proposal.model_dump_json())
    _seed_week(db, proposal, "approved", ref="plan-1")

    stored = plan_state.get_week(db, W)

    assert isinstance(stored, StoredWeek) and issubclass(StoredWeek, Contract)  # frozen models
    assert stored.proposal == proposal
    assert stored.proposal.recipe_options[0].mealie_slug == FAJITAS
    assert (stored.week_start, stored.status, stored.custody, stored.mealie_plan_ref) == (
        W,
        "approved",
        "wed+fri_sat",
        "plan-1",
    )
    assert stored.approved_at is not None
    assert plan_state.get_week(db, PRIOR) is None


def test_recent_proposals_are_approved_or_later_before_the_week_newest_first(
    db: sqlite3.Connection,
) -> None:
    statuses = {
        date(2026, 8, 23): "approved",
        date(2026, 8, 30): "approved",
        date(2026, 9, 6): "cart_filled",
        date(2026, 9, 13): "ordered",
        PRIOR: "proposed",
        W: "approved",
        date(2026, 10, 4): "approved",
    }
    proposals = {
        week: _proposal(
            week, f"Pick for {week}", slug=FAJITAS if week == date(2026, 9, 13) else None
        )
        for week in statuses
    }
    for week, status in statuses.items():
        _seed_week(db, proposals[week], status)
    newest_first = [proposals[date(2026, 9, 13)], proposals[date(2026, 9, 6)]]
    newest_first += [proposals[date(2026, 8, 30)], proposals[date(2026, 8, 23)]]

    assert plan_state.recent_proposals(db, W) == tuple(newest_first[:3])
    assert plan_state.recent_proposals(db, W, limit=10) == tuple(newest_first)
    assert plan_state.recent_proposals(db, date(2026, 8, 23)) == ()


def test_weeks_since_returns_every_status_from_the_date_oldest_first(
    db: sqlite3.Connection,
) -> None:
    filled = _proposal(W, slug=FAJITAS)
    _seed_week(db, _proposal(date(2026, 9, 13)), "approved")
    _seed_week(db, filled, "cart_filled", ref="plan-27")
    _seed_week(db, _proposal(PRIOR), "proposed")

    weeks = plan_state.weeks_since(db, PRIOR)

    assert isinstance(weeks, tuple)
    assert [(w.week_start, w.status) for w in weeks] == [(PRIOR, "proposed"), (W, "cart_filled")]
    assert (weeks[1].proposal, weeks[1].mealie_plan_ref) == (filled, "plan-27")


def test_get_run_reads_started_at_as_utc(db: sqlite3.Connection) -> None:
    # Seam map `JobRun`: started_at is UTC-aware, parsed from SQLite text (db.py:74-77).
    _seed_claim(
        db,
        "cart_fill",
        W,
        outcome="done",
        detail="report",
        started="2026-09-26 12:34:56",
        finished="2026-09-26 12:40:00",
    )

    run = plan_state.get_run(db, "cart_fill", W)

    assert isinstance(run, JobRun) and issubclass(JobRun, Contract)  # frozen models
    assert (run.job, run.week_start, run.outcome, run.detail) == ("cart_fill", W, "done", "report")
    assert run.started_at == datetime(2026, 9, 26, 12, 34, 56, tzinfo=UTC)
    assert run.started_at.utcoffset() == timedelta(0)
    assert run.finished_at is not None
    assert run.finished_at.replace(tzinfo=None) == datetime(2026, 9, 26, 12, 40)
    assert plan_state.get_run(db, "cart_fill", PRIOR) is None


def test_running_is_the_oldest_unfinished_claim_for_the_job_in_any_week(
    db: sqlite3.Connection,
) -> None:
    # Seam map `running`: the lock holder's week may not be the waiter's target.
    _seed_claim(db, "cart_fill", date(2026, 9, 13), outcome="done", started="2026-09-12 08:00:00")
    _seed_claim(db, "cart_fill", PRIOR, started="2026-09-26 13:05:00")
    _seed_claim(db, "cart_fill", W, started="2026-09-27 01:00:00")
    _seed_claim(db, "sat_propose", date(2026, 9, 6), started="2026-09-05 12:00:00")

    run = plan_state.running(db, "cart_fill")

    assert run is not None
    assert (run.week_start, run.outcome) == (PRIOR, None)
    assert run.started_at == datetime(2026, 9, 26, 13, 5, tzinfo=UTC)
    assert plan_state.running(db, "reconcile") is None


def test_latest_run_is_the_newest_week_with_one_of_the_outcomes(db: sqlite3.Connection) -> None:
    # Seam map `latest_run`; ADR :173 ("retry cart" picks the latest interrupted/failed week).
    _seed_claim(db, "cart_fill", date(2026, 9, 6), outcome="failed")
    _seed_claim(db, "cart_fill", date(2026, 9, 13), outcome="interrupted")
    _seed_claim(db, "cart_fill", PRIOR, outcome="done")
    _seed_claim(db, "cart_fill", W)
    _seed_claim(db, "sat_propose", W, outcome="failed")

    def latest(*outcomes: Outcome) -> date | None:
        run = plan_state.latest_run(db, "cart_fill", outcomes)
        return None if run is None else run.week_start

    assert latest("interrupted", "failed") == date(2026, 9, 13)
    assert latest("failed") == date(2026, 9, 6)
    assert latest("done") == PRIOR
    assert plan_state.latest_run(db, "sun_autoapprove", ("failed",)) is None
