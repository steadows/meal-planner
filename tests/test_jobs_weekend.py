"""meals.jobs.run_job for the weekend jobs: sat_propose, sat_nudge and sun_autoapprove.

Authority: the P2 seam map (.context/seams/P2.md, `meals/jobs.py`: the per-job rules, "Inspect is one
step", "The catch-all", D2, D6-D8, D10) and ADR-0001 (Jobs :82-99, Job contract :114-165, Retries
:171-179, Consequences :253-254, Integration Test Points :421-443). PLAN.md :24 and :666 say what the
proposal names; :34 and :77 give the nudge and the Sunday fallback.

Windows are half-open America/Detroit times. Each edge is a parameter row, and the DST rows pass
`now` in UTC on the 2026 shift weekends (Mar 8 and Nov 1), where a fixed UTC offset lands on the
wrong side of the edge. The process's own zone is set to Asia/Tokyo so a job that reads the
machine's zone shows up here, not only in CI.

Fakes are local: `send` records every attempt and, while `down`, raises DeliveryFailed as an
exhausted TelegramSend does; `spawn` records each argv; `propose` records (week, custody, recent)
and returns a fresh proposal. Rows are seeded and read back with SQL on a second connection.
"""

import logging
import os
import re
import signal
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from meals import background
from meals.contracts import (
    TRUSTED,
    CartReport,
    Components,
    Custody,
    MealieUnavailable,
    RecipeOption,
    WeekProposal,
)
from meals.fakes import FakeMealieClient
from meals.pantry import SqlitePantry

_MISSING: ImportError | None = None
try:
    from meals import jobs
except ImportError as exc:  # RED: meals/jobs.py isn't written yet
    _MISSING = exc

DETROIT = ZoneInfo("America/Detroit")
SAT = date(2026, 9, 26)  # conftest's TODAY
W = date(2026, 9, 27)
PRIOR = date(2026, 9, 20)
WEEK_ARGS = ("cart_fill", "--week", "2026-09-27")
SEED_START = "2026-09-26 12:00:00"
ALARM_S = 45 * 60  # ADR :150; seam map D9
FAJITAS = "sheet-pan-chicken-fajitas"  # a slug conftest's fake_mealie knows
NEW_PICK = "Gochujang turkey meatballs"  # what the propose stub plans
STORED_PICK = "Sheet-pan chicken fajitas"  # a proposal already stored for the week
LAST_PICK = "Slow-cooker white chicken chili"  # last week's approved plan
KID_NIGHT = "Wed cook-with-Miles (ravioli)"


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=DETROIT)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


SAT_9 = _local(2026, 9, 26, 9)
SUN_8 = _local(2026, 9, 27, 8)


# ── fakes and fixtures ───────────────────────────────────────────────────────


class Telegram:
    """The `send` stub: records every attempt; while `down` it raises DeliveryFailed."""

    def __init__(self) -> None:
        self.down = False
        self.attempts: list[str] = []
        self.delivered: list[str] = []

    def __call__(self, text: str) -> None:
        self.attempts.append(text)
        if self.down:
            raise background.DeliveryFailed("fake: Telegram is unreachable")
        self.delivered.append(text)


class Spawner:
    """The `spawn` stub: records each argv; the next `failures` calls raise OSError."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.failures = 0

    def __call__(self, name: str, *args: str) -> int:
        self.calls.append((name, *args))
        if self.failures:
            self.failures -= 1
            raise OSError("fake: couldn't start python")
        return 4242


class Proposer:
    """The `propose` stub: records (week, custody, recent), runs `before`, returns a new plan."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, str, list[WeekProposal]]] = []
        self.before: Callable[[], object] | None = None
        self.returns: WeekProposal | None = None  # a plan to return instead of a fresh one

    def __call__(
        self, week_start: date, custody: Custody, recent: Sequence[WeekProposal]
    ) -> WeekProposal:
        self.calls.append((week_start, custody, list(recent)))
        if self.before is not None:
            self.before()
        if self.returns is not None:
            return self.returns
        return _proposal(week_start, NEW_PICK, custody=custody)


def _no_fill(plan: WeekProposal, hold_fds: tuple[int, ...]) -> CartReport:
    raise AssertionError("the weekend jobs never fill a cart")


@dataclass(frozen=True)
class Job:
    """One test's view of a job process: its Deps, the stubs behind them, and a second
    connection for reading back what it committed."""

    db: sqlite3.Connection
    reader: sqlite3.Connection
    deps: "jobs.Deps"
    telegram: Telegram
    spawner: Spawner
    proposer: Proposer
    mealie: FakeMealieClient


@pytest.fixture(autouse=True)
def _jobs_exists() -> None:
    if _MISSING is not None:
        pytest.fail(
            f"meals.jobs is not built yet (P2 seam map, `meals/jobs.py`): {_MISSING}", pytrace=False
        )


@pytest.fixture(autouse=True)
def _system_clock_is_not_detroit() -> Iterator[None]:
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.fixture(autouse=True)
def _restore_signals() -> Iterator[None]:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGALRM)}
    yield
    signal.alarm(0)
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.fixture
def job(db: sqlite3.Connection, tmp_path: Path, fake_mealie: FakeMealieClient) -> Iterator[Job]:
    telegram, spawner, proposer = Telegram(), Spawner(), Proposer()
    deps = jobs.Deps(
        conn=db,
        lock_dir=tmp_path,
        send=telegram,
        spawn=spawner,
        propose=proposer,
        fill=_no_fill,
        mealie=fake_mealie,
    )
    reader = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()["file"])
    reader.row_factory = sqlite3.Row
    yield Job(db, reader, deps, telegram, spawner, proposer, fake_mealie)
    reader.close()


def _run(job: Job, name: "jobs.JobName", now: datetime, *, retry: bool = False) -> int:
    return jobs.run_job(name, job.deps, now=now, retry=retry)


def _option(name: str, slug: str | None) -> RecipeOption:
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
    pick: str = STORED_PICK,
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
        kid_nights=(KID_NIGHT,),
        pantry_questions=(),
    )


def _seed_week(
    job: Job,
    status: str,
    week: date = W,
    *,
    plan: WeekProposal | None = None,
    ref: str | None = None,
) -> WeekProposal:
    plan = plan or _proposal(week)
    job.db.execute(
        "INSERT INTO weekly_plan (week_start, custody, components, status, mealie_plan_ref,"
        " approved_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            plan.week_start.isoformat(),
            plan.custody,
            plan.model_dump_json(),
            status,
            ref,
            None if status == "proposed" else SEED_START,
        ),
    )
    job.db.commit()
    return plan


def _seed_claim(
    job: Job,
    name: str,
    week: date = W,
    *,
    outcome: str | None = None,
    detail: str | None = None,
    started: str = SEED_START,
) -> None:
    """A claim for job `name`; `outcome=None` leaves it unfinished."""
    job.db.execute(
        "INSERT INTO job_run (job, week_start, started_at, finished_at, outcome, detail)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, week.isoformat(), started, None if outcome is None else started, outcome, detail),
    )
    job.db.commit()


def _week(job: Job, week: date = W) -> sqlite3.Row:
    row: sqlite3.Row | None = job.reader.execute(
        "SELECT * FROM weekly_plan WHERE week_start = ?", (week.isoformat(),)
    ).fetchone()
    assert row is not None, f"no weekly_plan row for {week}"
    return row


def _claim(job: Job, name: str, week: date = W) -> sqlite3.Row:
    row: sqlite3.Row | None = job.reader.execute(
        "SELECT * FROM job_run WHERE job = ? AND week_start = ?", (name, week.isoformat())
    ).fetchone()
    assert row is not None, f"no {name} claim for {week}"
    return row


def _stored(row: sqlite3.Row) -> WeekProposal:
    return WeekProposal.model_validate_json(row["components"], context={TRUSTED: True})


def _snapshot(job: Job) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    runs = job.reader.execute("SELECT * FROM job_run ORDER BY job, week_start").fetchall()
    weeks = job.reader.execute("SELECT * FROM weekly_plan ORDER BY week_start").fetchall()
    return [tuple(r) for r in runs], [tuple(r) for r in weeks]


def _one_message(job: Job) -> str:
    assert len(job.telegram.delivered) == 1, job.telegram.delivered
    return job.telegram.delivered[0]


# ── sat_propose ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "week", "acts"),
    [
        pytest.param(_local(2026, 9, 26, 7, 59), W, False, id="sat-07:59"),
        pytest.param(_local(2026, 9, 26, 8, 0), W, True, id="sat-08:00"),
        pytest.param(_local(2026, 9, 26, 19, 59), W, True, id="sat-19:59"),
        pytest.param(_local(2026, 9, 26, 20, 0), W, False, id="sat-20:00"),
        pytest.param(_local(2026, 9, 25, 12, 0), W, False, id="fri-noon"),
        pytest.param(_local(2026, 9, 27, 12, 0), W, False, id="sun-noon"),
        pytest.param(_utc(2026, 3, 8, 0, 59), date(2026, 3, 8), True, id="dst-mar-sat-19:59-est"),
        pytest.param(_utc(2026, 3, 8, 1, 0), date(2026, 3, 8), False, id="dst-mar-sat-20:00-est"),
        pytest.param(_utc(2026, 11, 1, 0, 0), date(2026, 11, 1), False, id="dst-nov-sat-20:00-edt"),
    ],
)
def test_sat_propose_acts_only_inside_saturday_08_to_20(
    job: Job, now: datetime, week: date, acts: bool
) -> None:
    # ADR Jobs :86 and Integration Test Point :422; seam map D10. A job that decides not to act
    # writes nothing (ADR :136-137, Risk #1).
    _run(job, "sat_propose", now)

    if acts:
        assert [call[0] for call in job.proposer.calls] == [week]
        assert _week(job, week)["status"] == "proposed"
        assert _claim(job, "sat_propose", week)["outcome"] == "done"
        _one_message(job)
    else:
        assert (job.proposer.calls, job.telegram.attempts) == ([], [])
        assert _snapshot(job) == ([], [])


def test_sat_propose_plans_stores_the_proposal_sends_it_and_finishes(job: Job) -> None:
    # Seam map sat_propose row and D2 (the first week's custody is wed+sat_sun; open for Steve).
    # PLAN :24, :666: the message offers the recipes and names the kid nights.
    _run(job, "sat_propose", SAT_9)

    assert job.proposer.calls == [(W, "wed+sat_sun", [])]
    row = _week(job)
    assert (row["status"], row["custody"], row["approved_at"]) == ("proposed", "wed+sat_sun", None)
    assert _stored(row) == _proposal(W, NEW_PICK)
    assert _claim(job, "sat_propose")["outcome"] == "done"
    message = _one_message(job)
    assert NEW_PICK in message and KID_NIGHT in message


def test_sat_propose_plans_from_recent_weeks_with_the_latest_custody(job: Job) -> None:
    # Seam map: propose(W, custody, recent_proposals(W)) (the last 3 approved-or-later weeks, newest
    # first) and D2 (the most recent stored week's custody).
    plans = {
        date(2026, 8, 30): _seed_week(job, "approved", date(2026, 8, 30)),
        date(2026, 9, 6): _seed_week(job, "cart_filled", date(2026, 9, 6)),
        date(2026, 9, 13): _seed_week(job, "ordered", date(2026, 9, 13)),
        PRIOR: _seed_week(job, "approved", PRIOR, plan=_proposal(PRIOR, custody="wed+fri_sat")),
    }

    _run(job, "sat_propose", SAT_9)

    assert job.proposer.calls == [
        (W, "wed+fri_sat", [plans[PRIOR], plans[date(2026, 9, 13)], plans[date(2026, 9, 6)]])
    ]


def test_a_week_inserted_by_someone_else_mid_run_finishes_quietly(job: Job) -> None:
    # Seam map D6 (Risk #7): the insert conflicts → re-read → finish done, no message.
    racer = _proposal(W, "The racer's plan")
    job.proposer.before = lambda: _seed_week(job, "proposed", plan=racer)

    _run(job, "sat_propose", SAT_9)

    assert job.telegram.attempts == []
    assert _claim(job, "sat_propose")["outcome"] == "done"
    assert _stored(_week(job)) == racer


@pytest.mark.parametrize(
    ("stored", "now", "outcome", "expect"),
    [
        pytest.param(
            "proposed", _local(2026, 9, 26, 11), "done", STORED_PICK, id="resent-in-window"
        ),
        pytest.param("approved", _local(2026, 9, 26, 11), "failed", None, id="approved-not-resent"),
        pytest.param("proposed", _local(2026, 9, 26, 20, 30), "failed", None, id="after-20:00"),
        pytest.param(None, _local(2026, 9, 26, 20, 30), "interrupted", "resend plan", id="notice"),
    ],
)
def test_an_unfinished_sat_propose_claim_is_settled_before_the_window_is_checked(
    job: Job, stored: str | None, now: datetime, outcome: str, expect: str | None
) -> None:
    # ADR :124-135 (inspect before decide; the proposal is re-sent only while proposed and within
    # its window, else finish failed) and the sat_propose notice (:169); seam map "Inspect is one
    # step".
    if stored is not None:
        _seed_week(job, stored)
    _seed_claim(job, "sat_propose")

    _run(job, "sat_propose", now)

    assert job.proposer.calls == []
    assert _claim(job, "sat_propose")["outcome"] == outcome
    if expect is None:
        assert job.telegram.attempts == []
    else:
        assert expect in _one_message(job)


def test_resend_plan_after_an_interrupted_claim_plans_again(job: Job) -> None:
    # ADR Retries :175-180: "resend plan" is sat_propose --retry; the reset replaces Claim.
    _seed_claim(job, "sat_propose", outcome="interrupted", detail="planner timed out")

    _run(job, "sat_propose", _local(2026, 9, 26, 10), retry=True)

    assert len(job.proposer.calls) == 1
    assert _week(job)["status"] == "proposed"
    claim = _claim(job, "sat_propose")
    assert (claim["outcome"], claim["detail"]) == ("done", None)
    assert claim["started_at"] != SEED_START
    assert NEW_PICK in _one_message(job)


def test_resend_plan_with_a_stored_proposal_resends_it_without_planning(job: Job) -> None:
    # ADR :175-176: when the row exists it re-sends the stored proposal (no new planner run).
    _seed_week(job, "proposed")
    _seed_claim(job, "sat_propose", outcome="failed", detail="Telegram was down")

    _run(job, "sat_propose", _local(2026, 9, 26, 10), retry=True)

    assert job.proposer.calls == []
    assert STORED_PICK in _one_message(job)
    assert _claim(job, "sat_propose")["outcome"] == "done"


@pytest.mark.parametrize(
    ("outcome", "now", "reason"),
    [
        pytest.param("interrupted", _local(2026, 9, 26, 20, 30), "outside the saturday window"),
        pytest.param("done", _local(2026, 9, 26, 10), "nothing to retry"),
    ],
    ids=["after-20:00", "claim-done"],
)
def test_a_resend_plan_that_wont_act_sends_one_reason_and_writes_nothing(
    job: Job, outcome: str, now: datetime, reason: str
) -> None:
    # Seam map D7; ADR :176 (it keeps the Sat 20:00 window).
    if outcome == "done":
        _seed_week(job, "proposed")
    _seed_claim(job, "sat_propose", outcome=outcome)
    before = _snapshot(job)

    _run(job, "sat_propose", now, retry=True)

    assert job.proposer.calls == []
    assert _snapshot(job) == before
    assert reason in _one_message(job).lower()


def test_a_corrupt_pantry_row_reaches_steve_with_its_row_id_name_and_fix(job: Job) -> None:
    # ADR Integration Test Point :441-442; seam map, The catch-all (the reason's first line carries
    # PantryRowError's text). The real pantry reads a hand-corrupted row, as planner.propose does.
    job.db.execute(
        "INSERT INTO pantry_item (id, name, category, meijer_url)"
        " VALUES (7, 'olive oil', 'staple', 'https://evil.example.com/oil')"
    )
    job.db.commit()
    job.proposer.before = lambda: SqlitePantry(job.db).staples_due(SAT)

    _run(job, "sat_propose", SAT_9)

    message = _one_message(job)
    assert "job sat_propose failed: " in message
    assert "pantry_item 7" in message and "olive oil" in message
    assert "re-run the seed loader" in message
    claim = _claim(job, "sat_propose")
    assert claim["outcome"] == "failed" and "pantry_item 7" in claim["detail"]
    assert _snapshot(job)[1] == []


def test_the_alarm_is_armed_for_45_minutes_while_proposing(job: Job) -> None:
    # Seam map D9: a flat 45 min for every job in P2.
    armed: list[float] = []
    job.proposer.before = lambda: armed.append(signal.getitimer(signal.ITIMER_REAL)[0])

    _run(job, "sat_propose", SAT_9)

    assert len(armed) == 1 and ALARM_S - 60 < armed[0] <= ALARM_S, armed
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


# ── sat_nudge ────────────────────────────────────────────────────────────────


def _ready_to_nudge(
    job: Job,
    week: date,
    now: datetime,
    *,
    ago: timedelta = timedelta(hours=4),
    status: str = "proposed",
    propose_outcome: str | None = "done",
) -> None:
    """The week's proposal, and sat_propose's claim finished `ago` before `now`."""
    _seed_week(job, status, week)
    if propose_outcome != "missing":
        started = _utc_text(now - ago)
        _seed_claim(job, "sat_propose", week, outcome=propose_outcome, started=started)


@pytest.mark.parametrize(
    ("now", "week", "acts"),
    [
        pytest.param(_local(2026, 9, 26, 15, 59), W, False, id="sat-15:59"),
        pytest.param(_local(2026, 9, 26, 16, 0), W, True, id="sat-16:00"),
        pytest.param(_local(2026, 9, 27, 7, 59), W, True, id="sun-07:59"),
        pytest.param(_local(2026, 9, 27, 8, 0), W, False, id="sun-08:00"),
        pytest.param(_utc(2026, 11, 1, 12, 59), date(2026, 11, 1), True, id="dst-nov-sun-07:59"),
        pytest.param(_utc(2026, 3, 8, 12, 0), date(2026, 3, 8), False, id="dst-mar-sun-08:00"),
    ],
)
def test_sat_nudge_acts_only_from_saturday_16_to_sunday_08(
    job: Job, now: datetime, week: date, acts: bool
) -> None:
    # ADR Jobs :87; seam map sat_nudge row and D10 (at Sun 08:00 the nudge window is closed).
    _ready_to_nudge(job, week, now)
    before = _snapshot(job)

    _run(job, "sat_nudge", now)

    if acts:
        _one_message(job)
        assert _claim(job, "sat_nudge", week)["outcome"] == "done"
    else:
        assert job.telegram.attempts == []
        assert _snapshot(job) == before


@pytest.mark.parametrize(
    ("ago", "acts"),
    [(timedelta(hours=3), True), (timedelta(hours=3) - timedelta(seconds=1), False)],
    ids=["exactly-3h", "just-under-3h"],
)
def test_sat_nudge_waits_three_hours_after_the_proposal(
    job: Job, ago: timedelta, acts: bool
) -> None:
    # Seam map: sat_propose's claim done with finished_at ≤ now − 3 h (ADR :87, "≥ 3 h ago").
    now = _local(2026, 9, 26, 20)
    _ready_to_nudge(job, W, now, ago=ago)

    _run(job, "sat_nudge", now)

    assert len(job.telegram.delivered) == (1 if acts else 0)


@pytest.mark.parametrize(
    ("status", "propose_outcome"),
    [
        pytest.param("approved", "done", id="already-approved"),
        pytest.param("proposed", "failed", id="proposal-failed"),
        pytest.param("proposed", None, id="proposal-unfinished"),
        pytest.param("proposed", "missing", id="no-proposal-claim"),
    ],
)
def test_sat_nudge_needs_a_delivered_proposal_still_waiting(
    job: Job, status: str, propose_outcome: str | None
) -> None:
    now = _local(2026, 9, 26, 18)
    _ready_to_nudge(job, W, now, status=status, propose_outcome=propose_outcome)
    before = _snapshot(job)

    _run(job, "sat_nudge", now)

    assert job.telegram.attempts == []
    assert _snapshot(job) == before


def test_the_nudge_is_sent_once(job: Job) -> None:
    # ADR :73 (nudge once), PLAN :34 (one nudge).
    _ready_to_nudge(job, W, _local(2026, 9, 26, 16))

    for hour in (16, 17, 21):
        _run(job, "sat_nudge", _local(2026, 9, 26, hour))

    _one_message(job)


@pytest.mark.parametrize(
    ("status", "now", "sent", "outcome"),
    [
        pytest.param("proposed", _local(2026, 9, 26, 18), True, "done", id="still-owed"),
        pytest.param("approved", _local(2026, 9, 26, 18), False, "done", id="nothing-to-nudge"),
        pytest.param("proposed", _local(2026, 9, 27, 8, 30), False, "failed", id="window-closed"),
    ],
)
def test_an_unfinished_nudge_claim_is_resent_only_while_it_is_owed(
    job: Job, status: str, now: datetime, sent: bool, outcome: str
) -> None:
    # Seam map sat_nudge row: the claim is the effect; Inspect re-sends it while proposed and in
    # the window, finishes done when there's nothing to nudge, failed once the window has closed.
    _ready_to_nudge(job, W, _local(2026, 9, 26, 16), status=status)
    _seed_claim(job, "sat_nudge")

    _run(job, "sat_nudge", now)

    assert _claim(job, "sat_nudge")["outcome"] == outcome
    assert len(job.telegram.attempts) == len(job.telegram.delivered) == (1 if sent else 0)


# ── sun_autoapprove ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "week", "acts"),
    [
        pytest.param(_local(2026, 9, 27, 7, 59), W, False, id="sun-07:59"),
        pytest.param(_local(2026, 9, 27, 8, 0), W, True, id="sun-08:00"),
        pytest.param(_local(2026, 9, 27, 19, 59), W, True, id="sun-19:59"),
        pytest.param(_local(2026, 9, 27, 20, 0), W, False, id="sun-20:00"),
        pytest.param(_local(2026, 9, 26, 12, 0), W, False, id="sat-noon"),
        pytest.param(_utc(2026, 3, 8, 11, 59), date(2026, 3, 8), False, id="dst-mar-07:59-edt"),
        pytest.param(_utc(2026, 3, 8, 12, 0), date(2026, 3, 8), True, id="dst-mar-08:00-edt"),
        pytest.param(_utc(2026, 11, 1, 12, 59), date(2026, 11, 1), False, id="dst-nov-07:59-est"),
        pytest.param(_utc(2026, 11, 1, 13, 0), date(2026, 11, 1), True, id="dst-nov-08:00-est"),
    ],
)
def test_sun_autoapprove_acts_only_inside_sunday_08_to_20(
    job: Job, now: datetime, week: date, acts: bool
) -> None:
    # ADR Jobs :88 and Integration Test Point :422; seam map D10.
    last_week = week - timedelta(days=7)
    _seed_week(job, "cart_filled", last_week, plan=_proposal(last_week, LAST_PICK), ref="plan-1")
    _seed_week(job, "proposed", week)
    before = _snapshot(job)

    _run(job, "sun_autoapprove", now)

    if acts:
        assert _week(job, week)["status"] == "approved"
        assert _claim(job, "sun_autoapprove", week)["outcome"] == "done"
    else:
        assert (job.telegram.attempts, job.spawner.calls) == ([], [])
        assert _snapshot(job) == before


@pytest.mark.parametrize(
    ("row", "propose_outcome", "missed"),
    [
        pytest.param("proposed", "done", False, id="proposal-delivered"),
        pytest.param("proposed", "failed", True, id="proposal-not-delivered"),
        pytest.param("missing", "missing", True, id="no-proposal"),
    ],
)
def test_sun_autoapprove_approves_last_weeks_plan_spawns_the_fill_and_tells_steve(
    job: Job, row: str, propose_outcome: str, missed: bool
) -> None:
    # ADR :92-97; seam map sun_autoapprove row (last week restamped with this week's Sunday).
    last = _proposal(PRIOR, LAST_PICK, slug=FAJITAS, custody="wed+fri_sat")
    _seed_week(job, "cart_filled", PRIOR, plan=last, ref="plan-20")
    if row == "proposed":
        _seed_week(job, "proposed")
    if propose_outcome != "missing":
        _seed_claim(job, "sat_propose", outcome=propose_outcome)

    _run(job, "sun_autoapprove", SUN_8)

    stored = _week(job)
    assert stored["status"] == "approved" and stored["approved_at"] is not None
    assert _stored(stored) == _proposal(W, LAST_PICK, slug=FAJITAS, custody="wed+fri_sat")
    assert job.spawner.calls == [WEEK_ARGS]
    assert _claim(job, "sun_autoapprove")["outcome"] == "done"
    said_missed = re.search(r"didn.t reach you", _one_message(job), re.IGNORECASE) is not None
    assert said_missed is missed


def test_sun_autoapprove_reuses_the_newest_approved_week_when_one_was_missed(job: Job) -> None:
    # Seam map D8: last week's plan is the newest approved-or-later week before W.
    older = date(2026, 9, 13)
    _seed_week(job, "ordered", older, plan=_proposal(older, LAST_PICK), ref="plan-13")
    _seed_week(job, "proposed", PRIOR, plan=_proposal(PRIOR, "Soup nobody approved"))
    _seed_week(job, "proposed")

    _run(job, "sun_autoapprove", SUN_8)

    assert _stored(_week(job)) == _proposal(W, LAST_PICK)


def test_a_spawn_failure_after_approval_still_finishes_with_one_message(job: Job) -> None:
    # ADR Risk #12; seam map: an OSError is logged and the message says reconcile will retry.
    _seed_week(job, "cart_filled", PRIOR, plan=_proposal(PRIOR, LAST_PICK), ref="plan-20")
    _seed_week(job, "proposed")
    _seed_claim(job, "sat_propose", outcome="done")
    job.spawner.failures = 1

    _run(job, "sun_autoapprove", SUN_8)

    assert job.spawner.calls == [WEEK_ARGS]
    assert _week(job)["status"] == "approved"
    assert _claim(job, "sun_autoapprove")["outcome"] == "done"
    assert "retry" in _one_message(job).lower()


def test_the_first_week_sends_exactly_one_message_and_fails_the_claim(job: Job) -> None:
    # ADR :99, Risk #8 (and hourly repeats), Integration Test Point :439.
    _seed_week(job, "proposed")

    _run(job, "sun_autoapprove", SUN_8)
    _run(job, "sun_autoapprove", _local(2026, 9, 27, 9))

    _one_message(job)
    assert _claim(job, "sun_autoapprove")["outcome"] == "failed"
    assert (_week(job)["status"], job.spawner.calls) == ("proposed", [])


@pytest.mark.parametrize(
    ("status", "pick", "outcome", "messages"),
    [
        pytest.param("approved", LAST_PICK, "done", 1, id="recorded"),
        pytest.param("proposed", STORED_PICK, "interrupted", 1, id="not-recorded"),
        pytest.param("approved", STORED_PICK, "done", 0, id="approved-by-steve"),
    ],
)
def test_an_unfinished_autoapprove_claim_is_settled_by_inspect(
    job: Job, status: str, pick: str, outcome: str, messages: int
) -> None:
    # ADR :125-135: effect recorded (approved or later) → re-send, finish done; not recorded →
    # notice, then interrupted. Seam map D4: either way the run ends there. D16 (#8): it re-sends
    # only when the stored plan is last week's restamped, which is what this job writes; a week
    # Steve approved with other picks (after the job died before its compare-and-set) is quiet.
    _seed_week(job, "cart_filled", PRIOR, plan=_proposal(PRIOR, LAST_PICK), ref="plan-20")
    _seed_week(job, status, plan=_proposal(W, pick))
    _seed_claim(job, "sun_autoapprove")
    week_before = tuple(_week(job))

    _run(job, "sun_autoapprove", _local(2026, 9, 27, 9))

    assert _claim(job, "sun_autoapprove")["outcome"] == outcome
    assert len(job.telegram.attempts) == len(job.telegram.delivered) == messages
    assert tuple(_week(job)) == week_before


# ── end to end ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["sun_autoapprove", "sat_nudge"])
def test_no_claim_finishes_before_its_message_is_delivered(job: Job, name: "jobs.JobName") -> None:
    # ADR :147-149: a send that exhausts its retries leaves the claim unfinished, and the next tick
    # redelivers it through Inspect.
    if name == "sun_autoapprove":
        _seed_week(job, "cart_filled", PRIOR, plan=_proposal(PRIOR, LAST_PICK), ref="plan-20")
        _seed_week(job, "proposed")
        first, second = SUN_8, _local(2026, 9, 27, 9)
    else:
        first, second = _local(2026, 9, 26, 18), _local(2026, 9, 26, 19)
        _ready_to_nudge(job, W, first)
    job.telegram.down = True

    _run(job, name, first)

    assert _claim(job, name)["outcome"] is None, "no claim finishes before its message is delivered"
    job.telegram.down = False

    _run(job, name, second)

    _one_message(job)
    assert _claim(job, name)["outcome"] == "done"


def test_a_proposal_that_missed_saturday_is_never_sent_and_sunday_says_so(job: Job) -> None:
    # ADR Integration Test Point :431, Consequences :253-254, Risk #30.
    _seed_week(job, "cart_filled", PRIOR, plan=_proposal(PRIOR, LAST_PICK), ref="plan-20")
    job.telegram.down = True

    _run(job, "sat_propose", SAT_9)

    assert _claim(job, "sat_propose")["outcome"] is None
    job.telegram.down = False
    attempts = len(job.telegram.attempts)

    _run(job, "sat_propose", _local(2026, 9, 26, 20, 30))

    assert len(job.telegram.attempts) == attempts, "a proposal after Sat 20:00 is never sent"
    assert _claim(job, "sat_propose")["outcome"] == "failed"

    _run(job, "sun_autoapprove", SUN_8)

    assert re.search(r"didn.t reach you", _one_message(job), re.IGNORECASE)
    assert _week(job)["status"] == "approved"
    assert not any(NEW_PICK in text for text in job.telegram.delivered)


def test_a_fallback_week_publishes_last_weeks_plan(job: Job) -> None:
    # ADR Integration Test Point :436 and :75-80 (Steve cooks from the approved plan, including in
    # a fallback week). This week's own proposal has no slug and can't be published.
    _seed_week(job, "cart_filled", PRIOR, plan=_proposal(PRIOR, LAST_PICK, slug=FAJITAS), ref="p")
    _seed_week(job, "proposed")

    _run(job, "sun_autoapprove", SUN_8)
    _run(job, "reconcile", _local(2026, 9, 27, 9, 15))

    assert job.mealie.meal_plans[W] == (FAJITAS,)
    assert _week(job)["mealie_plan_ref"] == "fake-plan-2026-09-27"


# ── code-review repairs (seam map "Also fixed"; review findings #11, #13, #15) ─


def test_the_three_hour_wait_is_measured_in_real_time_across_the_dst_change(job: Job) -> None:
    # Review #11: from 01:30 EDT to 04:00 EST on Nov 1 is 3.5 real hours, but only 2.5 by Detroit
    # wall-clock arithmetic. (Seeded: a proposal finishing after Sat 20:00 isn't reachable today,
    # so this pins the arithmetic, not a live path.)
    week = date(2026, 11, 1)
    _seed_week(job, "proposed", week)
    _seed_claim(job, "sat_propose", week, outcome="done", started="2026-11-01 05:30:00")

    _run(job, "sat_nudge", _utc(2026, 11, 1, 9))

    _one_message(job)
    assert _claim(job, "sat_nudge", week)["outcome"] == "done"


def test_the_catch_all_logs_mealies_message_but_not_its_unvetted_cause(
    job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    # Review #13 (contracts.MealieUnavailable: log the message, not the chained cause).
    def mealie_down() -> None:
        raise MealieUnavailable("Mealie returned 503 for /api/recipes") from RuntimeError(
            "UNVETTED-CAUSE"
        )

    job.proposer.before = mealie_down
    caplog.set_level(logging.DEBUG)

    _run(job, "sat_propose", SAT_9)

    assert "Mealie returned 503 for /api/recipes" in caplog.text
    assert "UNVETTED-CAUSE" not in caplog.text


def test_a_recipe_with_no_hands_on_time_says_0_minutes(job: Job) -> None:
    # Review #15: hands_on_min=0 is known (no hands-on time), not unknown.
    plan = _proposal(W, NEW_PICK)
    quick = plan.recipe_options[0].model_copy(update={"hands_on_min": 0})
    job.proposer.returns = plan.model_copy(update={"recipe_options": (quick,)})

    _run(job, "sat_propose", SAT_9)

    assert "0 min hands-on" in _one_message(job)


def test_a_proposal_planned_past_20_00_is_stored_but_never_sent(job: Job) -> None:
    # D22 (C4): the job's current time is `now` plus the elapsed Deps.clock, so planning that starts
    # at 19:59 and ends after 20:00 isn't delivered (ADR :133). The row stays; the claim fails.
    # Sunday's autoapprove then says it didn't reach Steve (covered by the end-to-end test above).
    elapsed = [0.0]

    def plan_for_five_minutes() -> None:
        elapsed[0] += 5 * 60

    job = replace(job, deps=replace(job.deps, clock=lambda: elapsed[0]))
    job.proposer.before = plan_for_five_minutes

    _run(job, "sat_propose", _local(2026, 9, 26, 19, 59))

    assert len(job.proposer.calls) == 1 and job.telegram.attempts == []
    assert _week(job)["status"] == "proposed"
    claim = _claim(job, "sat_propose")
    assert claim["outcome"] == "failed"
    assert "not delivered" in (claim["detail"] or "")
