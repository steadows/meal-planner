"""meals.jobs.run_job for cart_fill and reconcile, and the job contract they exercise: the lock,
Inspect, the catch-all, signals and the alarm, result preservation and redelivery.

Authority: the P2 seam map (.context/seams/P2.md, `meals/jobs.py`: the symbol table, the cart_fill
and reconcile rows, "Inspect is one step", "The catch-all", "Busy lock", D3-D5, D7, D9) and ADR-0001
(Jobs :101-112, Job contract :114-165, Retries :171-183, Orphaned Chrome child :186-203, Integration
Test Points :421-444). PLAN.md :502 and :665 say what the cart report carries.

Fakes are local. `send` records every attempt and, while `down`, raises DeliveryFailed as an
exhausted TelegramSend does. `spawn` records each argv. `fill` records (plan, hold_fds), can run a
hook while "filling", and can fail. Mealie is conftest's FakeMealieClient. Rows are seeded and read
back with SQL through a second connection, never through plan_state. An autouse guard handler
records any SIGTERM or SIGALRM that reaches the test instead of run_job's own handler.
"""

import fcntl
import logging
import os
import signal
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn
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
    Substitution,
    WeekProposal,
)
from meals.fakes import FakeMealieClient

_MISSING: ImportError | None = None
try:
    from meals import jobs, plan_state
except ImportError as exc:  # RED: meals/jobs.py isn't written yet
    _MISSING = exc

DETROIT = ZoneInfo("America/Detroit")
W = date(2026, 9, 27)  # the Sunday after conftest's TODAY
PRIOR = date(2026, 9, 20)
WEEK_ARGS = ("cart_fill", "--week", "2026-09-27")
SEED_START = "2026-09-26 12:00:00"
ALARM_S = 45 * 60  # ADR :150; seam map D9
FAJITAS = "sheet-pan-chicken-fajitas"  # a slug conftest's fake_mealie knows
REPORT = CartReport(
    added=("chicken thighs (2 lb)", "corn tortillas"),
    substituted=(Substitution(wanted="cilantro", used="flat-leaf parsley"),),
    missing=("tahini",),
    subtotal_cents=4217,
)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=DETROIT)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


SUN_10 = _local(2026, 9, 27, 10)  # cook day, mid-morning
SUN_10_15 = _local(2026, 9, 27, 10, 15)  # the next reconcile tick
SUN_10_16 = _local(2026, 9, 27, 10, 16)  # the cart_fill it spawned


# ── fakes and fixtures ───────────────────────────────────────────────────────


class Telegram:
    """The `send` stub. `on_send` runs inside each attempt, before it succeeds or fails."""

    def __init__(self) -> None:
        self.down = False
        self.on_send: Callable[[str], object] | None = None
        self.attempts: list[str] = []
        self.delivered: list[str] = []

    def __call__(self, text: str) -> None:
        self.attempts.append(text)
        if self.on_send is not None:
            self.on_send(text)
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


class Filler:
    """The `fill` stub: records (plan, hold_fds), runs `during`, then raises `error` or returns
    REPORT."""

    def __init__(self) -> None:
        self.calls: list[tuple[WeekProposal, tuple[int, ...]]] = []
        self.during: Callable[[tuple[int, ...]], object] | None = None
        self.error: Exception | None = None
        self.report = REPORT

    def __call__(self, plan: WeekProposal, hold_fds: tuple[int, ...]) -> CartReport:
        self.calls.append((plan, tuple(hold_fds)))
        if self.during is not None:
            self.during(tuple(hold_fds))
        if self.error is not None:
            raise self.error
        return self.report


def _no_propose(week_start: date, custody: Custody, recent: Sequence[WeekProposal]) -> WeekProposal:
    raise AssertionError("cart_fill and reconcile never plan a week")


@dataclass(frozen=True)
class Job:
    """One test's view of a job process: the Deps it runs with, the stubs behind them, and a
    second connection for reading back what it committed."""

    db: sqlite3.Connection
    reader: sqlite3.Connection
    deps: "jobs.Deps"
    telegram: Telegram
    spawner: Spawner
    filler: Filler
    mealie: FakeMealieClient
    lock_dir: Path


class SignalGuard:
    """The handler a test installs before run_job: a signal it catches never reached run_job's."""

    def __init__(self) -> None:
        self.hits: list[int] = []
        self.handler = self._record  # one bound method, so `is` comparisons hold

    def _record(self, signum: int, frame: FrameType | None) -> None:
        self.hits.append(signum)
        raise RuntimeError(f"signal {signum} reached the test's handler, not run_job's")


@pytest.fixture(autouse=True)
def _jobs_exists() -> None:
    if _MISSING is not None:
        pytest.fail(
            f"meals.jobs is not built yet (P2 seam map, `meals/jobs.py`): {_MISSING}", pytrace=False
        )


@pytest.fixture(autouse=True)
def _system_clock_is_not_detroit() -> Iterator[None]:
    """Windows are America/Detroit whatever the machine's zone (CI runs in UTC)."""
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
def signal_guard() -> Iterator[SignalGuard]:
    guard = SignalGuard()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGALRM)}
    for sig in previous:
        signal.signal(sig, guard.handler)
    yield guard
    signal.alarm(0)
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.fixture
def job(db: sqlite3.Connection, tmp_path: Path, fake_mealie: FakeMealieClient) -> Iterator[Job]:
    telegram, spawner, filler = Telegram(), Spawner(), Filler()
    deps = jobs.Deps(
        conn=db,
        lock_dir=tmp_path,
        send=telegram,
        spawn=spawner,
        propose=_no_propose,
        fill=filler,
        mealie=fake_mealie,
    )
    reader = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()["file"])
    reader.row_factory = sqlite3.Row
    yield Job(db, reader, deps, telegram, spawner, filler, fake_mealie, tmp_path)
    reader.close()


def _run(
    job: Job, name: "jobs.JobName", now: datetime, week: date | None = None, *, retry: bool = False
) -> int:
    return jobs.run_job(name, job.deps, now=now, week=week, retry=retry)


def _run_spawned(job: Job, now: datetime, *, since: int = 0) -> None:
    """Run each child `spawn` was asked for from call `since` on, as the detached process would."""
    for name, *args in job.spawner.calls[since:]:
        assert (name, args[0]) == ("cart_fill", "--week"), job.spawner.calls
        _run(job, "cart_fill", now, date.fromisoformat(args[1]), retry="--retry" in args)


def _option(name: str, *, slug: str | None = None, url: str | None = None) -> RecipeOption:
    return RecipeOption.model_validate(
        {
            "name": name,
            "url": url or "https://example.com/" + name.lower().replace(" ", "-"),
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
    week: date, pick: str = "Sheet-pan chicken fajitas", *, slug: str | None = FAJITAS
) -> WeekProposal:
    """A narrowed, approved-style plan. With `slug=None` its pick can't be published: conftest's
    fake Mealie scrapes no URL."""
    return WeekProposal(
        week_start=week,
        custody="wed+sat_sun",
        recipe_options=(_option(pick, slug=slug),),
        components=Components(proteins=("chicken thighs",), grains=("brown rice",)),
        lunch_builds=("Chicken sweet-potato bowl",),
        kid_nights=("Wed cook-with-Miles (ravioli)",),
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
    week: date = W,
    *,
    outcome: str | None = None,
    detail: str | None = None,
    started: str = SEED_START,
) -> None:
    """A cart_fill claim; `outcome=None` leaves it unfinished."""
    job.db.execute(
        "INSERT INTO job_run (job, week_start, started_at, finished_at, outcome, detail)"
        " VALUES ('cart_fill', ?, ?, ?, ?, ?)",
        (week.isoformat(), started, None if outcome is None else started, outcome, detail),
    )
    job.db.commit()


def _week(job: Job, week: date = W) -> sqlite3.Row:
    row: sqlite3.Row | None = job.reader.execute(
        "SELECT * FROM weekly_plan WHERE week_start = ?", (week.isoformat(),)
    ).fetchone()
    assert row is not None, f"no weekly_plan row for {week}"
    return row


def _claim(job: Job, week: date = W) -> sqlite3.Row:
    row: sqlite3.Row | None = job.reader.execute(
        "SELECT * FROM job_run WHERE job = 'cart_fill' AND week_start = ?", (week.isoformat(),)
    ).fetchone()
    assert row is not None, f"no cart_fill claim for {week}"
    return row


def _snapshot(job: Job) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    runs = job.reader.execute("SELECT * FROM job_run ORDER BY job, week_start").fetchall()
    weeks = job.reader.execute("SELECT * FROM weekly_plan ORDER BY week_start").fetchall()
    return [tuple(r) for r in runs], [tuple(r) for r in weeks]


def _one_message(job: Job) -> str:
    assert len(job.telegram.delivered) == 1, job.telegram.delivered
    return job.telegram.delivered[0]


@contextmanager
def _held(path: Path) -> Iterator[None]:
    """Hold `path`'s flock from this test, as another job process would."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _relocks(fd: int) -> bool:
    """True if `fd` can take the exclusive lock now: it holds it, or nobody does."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _lock_is_held(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR)
    try:
        return not _relocks(fd)
    finally:
        os.close(fd)


def _same_file(fd: int, path: Path) -> bool:
    try:
        opened = os.fstat(fd)
    except OSError:
        return False
    target = os.stat(path)
    return (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino)


# ── cart_fill ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "mealie_line"),
    [(None, "pending, retrying hourly"), ("plan-27", "Mealie plan: published")],
    ids=["mealie-pending", "mealie-published"],
)
def test_cart_fill_fills_records_the_report_sends_it_and_finishes(
    job: Job, ref: str | None, mealie_line: str
) -> None:
    # Seam map, cart_fill row; ADR :101-106. The stored plan reaches fill with its trusted slug.
    plan = _seed_week(job, "approved", ref=ref)

    _run(job, "cart_fill", SUN_10, W)

    assert [call[0] for call in job.filler.calls] == [plan]
    assert _week(job)["status"] == "cart_filled"
    assert _claim(job)["outcome"] == "done"
    assert CartReport.model_validate_json(_claim(job)["detail"]) == REPORT
    report = _one_message(job)
    assert mealie_line in report
    assert "tahini" in report and "42.17" in report  # PLAN :502, :665: the gaps and the subtotal
    lock = job.lock_dir / "job-cart_fill.lock"
    assert lock.exists(), "lock files are never deleted (ADR :118-119)"
    assert not _lock_is_held(lock), "the job lock is released when the run ends"


def test_fill_gets_the_job_lock_fd_and_the_lock_is_held_while_it_runs(job: Job) -> None:
    # ADR :190-195 and the seam map: fill(stored.proposal, (lock_fd,)), so an orphaned claude child
    # keeps the job lock. `_relocks` succeeds only on the descriptor that holds the lock (or a dup
    # of it), never on a fresh open of the same path.
    lock = job.lock_dir / "job-cart_fill.lock"
    seen: list[tuple[int, bool, bool]] = []

    def while_filling(hold_fds: tuple[int, ...]) -> None:
        lock_fds = [fd for fd in hold_fds if _same_file(fd, lock)]
        seen.append((len(lock_fds), _lock_is_held(lock), all(_relocks(fd) for fd in lock_fds)))

    job.filler.during = while_filling
    _seed_week(job, "approved")

    _run(job, "cart_fill", SUN_10, W)

    assert len(seen) == 1
    lock_fds, held, holds_it = seen[0]
    assert lock_fds >= 1, "hold_fds must carry an fd on job-cart_fill.lock"
    assert held, "the job lock must be held while fill runs"
    assert holds_it, "the fd passed in hold_fds must be the one holding the lock"


@pytest.mark.parametrize(
    ("now", "expired"),
    [
        pytest.param(_local(2026, 9, 27, 23, 59), False, id="sun-23:59"),
        pytest.param(_utc(2026, 9, 28, 3, 20), False, id="sun-23:20-local-is-monday-in-utc"),
        pytest.param(_local(2026, 9, 28, 0, 0), True, id="mon-00:00"),
        pytest.param(_local(2026, 9, 28, 0, 16), True, id="mon-00:16"),
    ],
)
def test_cart_fill_expires_once_the_cook_day_has_passed(
    job: Job, now: datetime, expired: bool
) -> None:
    # Seam map cart_fill: expired when W < the local date; ADR :101-102, Risk #22.
    _seed_week(job, "approved")

    _run(job, "cart_fill", now, W)

    if expired:
        assert job.filler.calls == []
        assert (_week(job)["status"], _claim(job)["outcome"]) == ("approved", "failed")
        message = _one_message(job)
        assert "never filled" in message and "retry cart" in message
    else:
        assert len(job.filler.calls) == 1
        assert _week(job)["status"] == "cart_filled"


@pytest.mark.parametrize("status", ["proposed", "missing"])
def test_cart_fill_writes_nothing_for_a_week_that_is_not_approved(job: Job, status: str) -> None:
    # ADR :136-137: a job that decides not to act writes nothing.
    if status != "missing":
        _seed_week(job, status)
    before = _snapshot(job)

    _run(job, "cart_fill", SUN_10, W)

    assert job.filler.calls == []
    assert _snapshot(job) == before


def test_a_finished_claim_exits_0_and_does_nothing(job: Job) -> None:
    # ADR :124: outcome already set → exit 0 (except --retry).
    _seed_week(job, "cart_filled", ref="plan-27")
    _seed_claim(job, outcome="done", detail=REPORT.model_dump_json())
    before = _snapshot(job)

    assert _run(job, "cart_fill", SUN_10, W) == 0

    assert (job.telegram.attempts, job.filler.calls) == ([], [])
    assert _snapshot(job) == before


# ── retry cart ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["interrupted", "failed"])
def test_retry_cart_resets_an_interrupted_or_failed_claim_and_fills(
    job: Job, outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR Retries :173-180 (the prior row goes to the job log before the reset); seam map
    # reset_for_retry.
    caplog.set_level(logging.DEBUG)
    _seed_week(job, "approved")
    _seed_claim(job, outcome=outcome, detail="Meijer asked for a login")

    _run(job, "cart_fill", SUN_10, W, retry=True)

    assert "Meijer asked for a login" in caplog.text, "the prior row is logged before the reset"
    assert len(job.filler.calls) == 1
    claim = _claim(job)
    assert claim["outcome"] == "done"
    assert CartReport.model_validate_json(claim["detail"]) == REPORT
    assert claim["started_at"] != SEED_START, "the reset restarts the claim"
    assert _week(job)["status"] == "cart_filled"
    _one_message(job)


@pytest.mark.parametrize(
    ("status", "outcome", "reason"),
    [
        pytest.param("approved", None, "nothing to retry", id="no-claim"),
        pytest.param("cart_filled", "failed", "already filled", id="already-filled"),
        pytest.param("cart_filled", "done", None, id="filled-and-done"),
    ],
)
def test_a_retry_that_wont_act_sends_one_reason_and_writes_nothing(
    job: Job, status: str, outcome: str | None, reason: str | None
) -> None:
    # Seam map D7; ADR Integration Test Point :437 (--retry on a cart_filled week writes nothing).
    _seed_week(job, status, ref="plan-27")
    if outcome is not None:
        _seed_claim(job, outcome=outcome, detail=REPORT.model_dump_json())
    before = _snapshot(job)

    _run(job, "cart_fill", SUN_10, W, retry=True)

    assert job.filler.calls == []
    assert _snapshot(job) == before
    message = _one_message(job)
    if reason is not None:
        assert reason in message.lower()


def test_retry_cart_on_an_unfinished_claim_sends_the_partway_notice_and_does_not_fill(
    job: Job,
) -> None:
    # Seam map D4: it must not fill over a cart that hasn't been emptied. Notice text: ADR :167-168.
    _seed_week(job, "approved")
    _seed_claim(job)

    _run(job, "cart_fill", SUN_10, W, retry=True)

    assert job.filler.calls == []
    assert _claim(job)["outcome"] == "interrupted"
    notice = _one_message(job)
    assert "stopped partway" in notice and "retry cart" in notice


# ── the lock ─────────────────────────────────────────────────────────────────


def test_a_busy_retry_says_since_when_in_local_time(job: Job) -> None:
    # Seam map, Busy lock and D3: the time comes from `running(conn, job)`, and the running claim
    # may be another week's. 14:35 UTC is 10:35 in Detroit.
    _seed_week(job, "approved")
    _seed_claim(job, PRIOR, started="2026-09-27 14:35:00")
    before = _snapshot(job)

    with _held(job.lock_dir / "job-cart_fill.lock"):
        _run(job, "cart_fill", _local(2026, 9, 27, 10, 50), W, retry=True)

    assert job.filler.calls == []
    assert _snapshot(job) == before
    message = _one_message(job)
    assert "still running" in message and "10:35" in message and "14:35" not in message


@pytest.mark.parametrize(
    ("age", "alerts"),
    [(timedelta(minutes=10), False), (timedelta(minutes=46), True)],
    ids=["fresh-quiet", "stale-alert"],
)
def test_a_busy_lock_is_quiet_unless_the_running_claim_is_stale(
    job: Job, age: timedelta, alerts: bool
) -> None:
    # Seam map, Busy lock; ADR :121-122. `now` is months before the real clock, so the age must be
    # measured from the injected `now`.
    week, now = date(2026, 3, 1), _local(2026, 3, 1, 10)
    _seed_week(job, "approved", week)
    _seed_claim(job, week, started=_utc_text(now - age))
    before = _snapshot(job)

    with _held(job.lock_dir / "job-cart_fill.lock"):
        _run(job, "cart_fill", now, week)

    assert job.filler.calls == []
    assert _snapshot(job) == before
    if alerts:
        assert "deploy/README.md" in _one_message(job)
    else:
        assert job.telegram.attempts == []


def test_reconcile_with_its_lock_busy_does_nothing(job: Job) -> None:
    _seed_week(job, "approved")
    before = _snapshot(job)

    with _held(job.lock_dir / "job-reconcile.lock"):
        _run(job, "reconcile", SUN_10_15)

    assert (job.telegram.attempts, job.spawner.calls, job.mealie.meal_plans) == ([], [], {})
    assert _snapshot(job) == before


# ── Inspect and redelivery ───────────────────────────────────────────────────


def test_the_interrupted_notice_is_sent_before_the_claim_is_marked(job: Job) -> None:
    """ADR :134-135 (send the notice, THEN mark interrupted) and Integration Test Point :424 (death
    after sending the notice but before marking → re-sent, then marked). A death right after a
    delivered notice leaves the claim just as a failed send does, unfinished. So the failed notice
    must leave the claim unfinished, the next tick must send it again, and neither send may see the
    claim already marked."""
    _seed_week(job, "approved")
    _seed_claim(job)
    marked_at_send: list[object] = []
    job.telegram.on_send = lambda text: marked_at_send.append(
        job.db.execute(
            "SELECT outcome FROM job_run WHERE job = 'cart_fill' AND week_start = ?",
            (W.isoformat(),),
        ).fetchone()[0]
    )
    job.telegram.down = True

    _run(job, "cart_fill", SUN_10_15, W)

    assert _claim(job)["outcome"] is None
    job.telegram.down = False

    _run(job, "cart_fill", _local(2026, 9, 27, 11, 15), W)

    assert job.filler.calls == []
    assert marked_at_send == [None, None]
    assert _claim(job)["outcome"] == "interrupted"
    assert len(job.telegram.attempts) == 2
    assert all("stopped partway" in text for text in job.telegram.attempts)


def _sigterm_on_first_send() -> Callable[[str], None]:
    sent: list[str] = []

    def on_send(text: str) -> None:
        if not sent:
            sent.append(text)
            os.kill(os.getpid(), signal.SIGTERM)

    return on_send


@pytest.mark.parametrize("failure", ["telegram_exhausted", "sigterm"])
def test_after_the_result_commits_a_failed_report_is_redelivered_without_refilling(
    job: Job, signal_guard: SignalGuard, failure: str
) -> None:
    # ADR Integration Test Point :427-429, Risk #23, result preservation :161-164; seam map D5.
    _seed_week(job, "approved")
    if failure == "telegram_exhausted":
        job.telegram.down = True
    else:
        job.telegram.on_send = _sigterm_on_first_send()

    _run(job, "cart_fill", SUN_10, W)

    committed = _claim(job)
    assert committed["outcome"] is None, "no claim finishes before its message is delivered"
    assert CartReport.model_validate_json(committed["detail"]) == REPORT
    assert _week(job)["status"] == "cart_filled"
    assert job.telegram.delivered == []
    job.telegram.down, job.telegram.on_send = False, None

    _run(job, "reconcile", SUN_10_15)
    assert job.spawner.calls == [WEEK_ARGS]
    _run_spawned(job, SUN_10_16)

    assert len(job.filler.calls) == 1
    assert "tahini" in _one_message(job)
    assert _claim(job)["outcome"] == "done"
    assert _claim(job)["detail"] == committed["detail"], "the committed result is byte-identical"
    assert signal_guard.hits == []


def test_a_failure_notice_that_cannot_be_sent_is_reported_by_the_next_tick_with_its_reason(
    job: Job,
) -> None:
    # ADR :156 and Integration Test Point :430; seam map, The catch-all and Inspect.
    _seed_week(job, "approved")
    job.filler.error = RuntimeError("Meijer asked for a login\n<html>Sign in</html>")
    job.telegram.down = True

    _run(job, "cart_fill", SUN_10, W)

    assert (_claim(job)["outcome"], _claim(job)["detail"]) == (None, "Meijer asked for a login")
    assert len(job.telegram.attempts) == 1
    assert "job cart_fill failed: Meijer asked for a login" in job.telegram.attempts[0]
    job.telegram.down, job.filler.error = False, None

    _run(job, "reconcile", SUN_10_15)
    _run_spawned(job, SUN_10_16)

    assert len(job.filler.calls) == 1
    notice = _one_message(job)
    assert "stopped partway" in notice and "Meijer asked for a login" in notice
    assert (_claim(job)["outcome"], _claim(job)["detail"]) == (
        "interrupted",
        "Meijer asked for a login",
    )


# ── the catch-all, signals and the alarm ─────────────────────────────────────


class QuietFailure(Exception):
    pass


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(
            RuntimeError("Meijer asked for a login\n<html>x</html>"), "Meijer asked for a login"
        ),
        pytest.param(QuietFailure(), "QuietFailure"),
    ],
    ids=["first-line", "type-name"],
)
def test_the_catch_all_writes_the_reason_sends_it_then_fails_the_claim(
    job: Job, error: Exception, reason: str
) -> None:
    # Seam map, The catch-all: it first disarms the alarm; not recorded → set_detail(reason) → send
    # → finish('failed').
    _seed_week(job, "approved")
    job.filler.error = error
    alarm_left: list[float] = []
    job.telegram.on_send = lambda text: alarm_left.append(signal.getitimer(signal.ITIMER_REAL)[0])

    _run(job, "cart_fill", SUN_10, W)

    assert (_claim(job)["outcome"], _claim(job)["detail"]) == ("failed", reason)
    assert _week(job)["status"] == "approved"
    assert f"job cart_fill failed: {reason}" in _one_message(job)
    assert alarm_left == [0.0], "the alarm must be disarmed before the failure is reported"


def test_the_catch_all_reason_is_capped_at_500_characters(job: Job) -> None:
    _seed_week(job, "approved")
    job.filler.error = RuntimeError("Mealie said: " + "slow " * 200)

    _run(job, "cart_fill", SUN_10, W)

    reason = _claim(job)["detail"]
    assert reason.startswith("Mealie said: slow slow") and len(reason) <= 500
    assert f"job cart_fill failed: {reason}" in _one_message(job)


def test_a_failure_before_the_claim_is_reported_writes_nothing_and_exits_1(job: Job) -> None:
    # Seam map, The catch-all: no claim of ours yet → message, nothing written, exit 1. A
    # hand-corrupted `components` fails when Decide reads the week.
    job.db.execute(
        "INSERT INTO weekly_plan (week_start, custody, components, status)"
        " VALUES (?, 'wed+sat_sun', '{\"not\": \"a proposal\"}', 'approved')",
        (W.isoformat(),),
    )
    job.db.commit()
    before = _snapshot(job)

    assert _run(job, "cart_fill", SUN_10, W) == 1

    assert job.filler.calls == []
    assert _snapshot(job) == before
    assert "job cart_fill failed: " in _one_message(job)


def test_sigterm_while_filling_goes_through_the_catch_all(
    job: Job, signal_guard: SignalGuard
) -> None:
    # ADR :150-153 and Integration Test Point :440; seam map: SIGTERM becomes JobTerminated.
    _seed_week(job, "approved")
    job.filler.during = lambda hold_fds: os.kill(os.getpid(), signal.SIGTERM)

    _run(job, "cart_fill", SUN_10, W)

    assert signal_guard.hits == []
    claim = _claim(job)
    assert claim["outcome"] == "failed" and claim["detail"]
    assert _week(job)["status"] == "approved"
    assert f"job cart_fill failed: {claim['detail']}" in _one_message(job)


def test_the_alarm_is_armed_for_45_minutes_and_sigalrm_goes_through_the_catch_all(
    job: Job, signal_guard: SignalGuard
) -> None:
    # ADR :150; seam map D9 (a flat 45 min in P2) and The catch-all (JobTimeout).
    armed: list[float] = []

    def while_filling(hold_fds: tuple[int, ...]) -> None:
        armed.append(signal.getitimer(signal.ITIMER_REAL)[0])
        os.kill(os.getpid(), signal.SIGALRM)

    job.filler.during = while_filling
    _seed_week(job, "approved")

    _run(job, "cart_fill", SUN_10, W)

    assert signal_guard.hits == []
    assert len(armed) == 1 and ALARM_S - 60 < armed[0] <= ALARM_S, armed
    assert _claim(job)["outcome"] == "failed"
    assert "job cart_fill failed: " in _one_message(job)
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


@pytest.mark.parametrize("path", ["fills", "fails", "lock-busy"])
def test_run_job_installs_its_own_handlers_and_restores_the_callers(
    job: Job, signal_guard: SignalGuard, path: str
) -> None:
    # Seam map `run_job`: handlers installed on entry and restored on exit; signal.alarm(0) on exit.
    _seed_week(job, "approved")
    own_handlers: list[bool] = []
    job.filler.during = lambda hold_fds: own_handlers.append(
        signal.getsignal(signal.SIGTERM) is not signal_guard.handler
        and signal.getsignal(signal.SIGALRM) is not signal_guard.handler
    )
    if path == "fails":
        job.filler.error = RuntimeError("Meijer asked for a login")
    busy = _held(job.lock_dir / "job-cart_fill.lock") if path == "lock-busy" else nullcontext()

    with busy:
        _run(job, "cart_fill", SUN_10, W)

    assert own_handlers == ([] if path == "lock-busy" else [True])
    assert signal.getsignal(signal.SIGTERM) is signal_guard.handler
    assert signal.getsignal(signal.SIGALRM) is signal_guard.handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


# ── reconcile ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "outcome", "spawns"),
    [
        pytest.param("proposed", "no-claim", False, id="proposed"),
        pytest.param("approved", "no-claim", True, id="approved-no-claim"),
        pytest.param("approved", None, True, id="approved-unfinished"),
        pytest.param("approved", "failed", False, id="approved-failed"),
        pytest.param("approved", "interrupted", False, id="approved-interrupted"),
        pytest.param("cart_filled", None, True, id="filled-unfinished-D5"),
        pytest.param("cart_filled", "done", False, id="filled-done"),
        pytest.param("ordered", None, True, id="ordered-unfinished-D19"),
    ],
)
def test_reconcile_spawns_a_fill_only_for_weeks_that_need_one(
    job: Job, status: str, outcome: str | None, spawns: bool
) -> None:
    # Seam map reconcile row (2) and D5; ADR :112. It claims nothing (ADR :108).
    _seed_week(job, status, ref=None if status == "proposed" else "plan-27")
    if outcome != "no-claim":
        _seed_claim(job, outcome=outcome, detail="earlier")
    claims_before = _snapshot(job)[0]

    _run(job, "reconcile", SUN_10_15)

    assert job.spawner.calls == ([WEEK_ARGS] if spawns else [])
    assert _snapshot(job)[0] == claims_before
    assert job.mealie.meal_plans == {}, "a proposed week, or one already published, isn't published"


@pytest.mark.parametrize(
    ("now", "scanned"),
    [
        pytest.param(_local(2026, 10, 3, 12), True, id="sat-six-days-after"),
        pytest.param(_utc(2026, 10, 4, 2), True, id="sat-22:00-local-is-sunday-in-utc"),
        pytest.param(_local(2026, 10, 4, 0, 15), False, id="sun-seven-days-after"),
    ],
)
def test_reconcile_scans_weeks_from_six_days_before_the_local_date(
    job: Job, now: datetime, scanned: bool
) -> None:
    # Seam map reconcile: weeks_since(today − 6); ADR :90.
    _seed_week(job, "approved")

    _run(job, "reconcile", now)

    ref = _week(job)["mealie_plan_ref"]
    if scanned:
        assert job.spawner.calls == [WEEK_ARGS]
        assert (ref, job.mealie.meal_plans[W]) == ("fake-plan-2026-09-27", (FAJITAS,))
    else:
        assert (job.spawner.calls, job.mealie.meal_plans, ref) == ([], {}, None)


def test_one_weeks_failure_does_not_stop_reconcile(job: Job) -> None:
    # Seam map reconcile: a failure is logged, and one week's failure doesn't stop the others.
    # PRIOR's only pick has no slug and a URL Mealie can't scrape; its spawn fails too.
    _seed_week(job, "approved", PRIOR, plan=_proposal(PRIOR, "Unscrapable stew", slug=None))
    _seed_week(job, "approved")
    job.spawner.failures = 1

    _run(job, "reconcile", _local(2026, 9, 26, 18, 15))

    assert sorted(job.spawner.calls) == [("cart_fill", "--week", "2026-09-20"), WEEK_ARGS]
    assert _week(job, PRIOR)["mealie_plan_ref"] is None
    assert _week(job)["mealie_plan_ref"] == "fake-plan-2026-09-27"


class MealieDown(FakeMealieClient):
    """set_meal_plan raises MealieUnavailable the next `failures` times, then works."""

    def __init__(self, failures: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failures = failures

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        if self.failures:
            self.failures -= 1
            raise MealieUnavailable("fake: Mealie is down")
        return super().set_meal_plan(week_start, slugs)


def test_mealie_down_on_the_first_reconcile_is_published_by_the_next_and_fill_runs_once(
    job: Job, sample_recipe: RecipeOption
) -> None:
    # ADR Integration Test Point :434-435; seam map reconcile (1): slug = opt.mealie_slug or
    # mealie.import_url(opt.url). The slugged pick's URL isn't scrapable, so it must not be imported.
    web_url = "https://example.com/turkey-taco-meat"
    mealie = MealieDown(
        1,
        recipes={FAJITAS: sample_recipe},
        importable={web_url: sample_recipe.model_copy(update={"name": "Turkey taco meat"})},
    )
    job = replace(job, deps=replace(job.deps, mealie=mealie), mealie=mealie)
    plan = _proposal(W)
    web = _option("Turkey taco meat", url=web_url)
    _seed_week(
        job,
        "approved",
        plan=plan.model_copy(update={"recipe_options": (*plan.recipe_options, web)}),
    )

    _run(job, "reconcile", _local(2026, 9, 27, 9, 15))
    assert _week(job)["mealie_plan_ref"] is None
    _run_spawned(job, _local(2026, 9, 27, 9, 16))
    spawned = len(job.spawner.calls)
    _run(job, "reconcile", SUN_10_15)
    _run_spawned(job, SUN_10_16, since=spawned)

    assert _week(job)["mealie_plan_ref"] == "fake-plan-2026-09-27"
    published = mealie.meal_plans[W]
    assert len(published) == 2 and FAJITAS in published
    other = next(slug for slug in published if slug != FAJITAS)
    assert mealie.recipes[other].name == "Turkey taco meat"
    assert len(job.filler.calls) == 1


# ── crash recovery (ADR Integration Test Points :432-433) ────────────────────


def test_an_approval_late_saturday_then_a_crash_is_filled_by_sundays_reconcile(job: Job) -> None:
    # Approved Sat 23:50; the bot died before spawning cart_fill (Risk #15).
    _seed_week(job, "approved")

    _run(job, "reconcile", _local(2026, 9, 27, 0, 15))
    assert job.spawner.calls == [WEEK_ARGS]
    _run_spawned(job, _local(2026, 9, 27, 0, 16))

    assert len(job.filler.calls) == 1
    assert (_week(job)["status"], _claim(job)["outcome"]) == ("cart_filled", "done")


def test_an_approval_late_sunday_then_a_crash_expires_once_then_retry_cart_fills(
    job: Job,
) -> None:
    # Approved Sun 23:20; the bot died before spawning cart_fill (Risk #22).
    _seed_week(job, "approved")

    _run(job, "reconcile", _local(2026, 9, 28, 0, 15))
    _run_spawned(job, _local(2026, 9, 28, 0, 16))
    spawned = len(job.spawner.calls)
    _run(job, "reconcile", _local(2026, 9, 28, 1, 15))
    _run_spawned(job, _local(2026, 9, 28, 1, 16), since=spawned)

    assert job.filler.calls == []
    assert "never filled" in _one_message(job)
    assert _claim(job)["outcome"] == "failed"

    _run(job, "cart_fill", _local(2026, 9, 28, 8), W, retry=True)

    assert len(job.filler.calls) == 1
    assert (_week(job)["status"], _claim(job)["outcome"]) == ("cart_filled", "done")
    assert CartReport.model_validate_json(_claim(job)["detail"]) == REPORT


# ── code-review repairs (seam map D12-D18; review findings #1-#16) ───────────


def test_a_failed_fill_tells_steve_to_empty_the_cart_before_retrying(job: Job) -> None:
    # D12 (#1): a failure mid-fill can leave items in the cart, and --retry accepts `failed`
    # (ADR risk #16), so the failure message carries the partway advice too.
    _seed_week(job, "approved")
    job.filler.error = RuntimeError("Meijer asked for a login")

    _run(job, "cart_fill", SUN_10, W)

    message = _one_message(job)
    assert "job cart_fill failed: Meijer asked for a login" in message
    assert "empty it" in message.lower() and "retry cart" in message


def test_the_stale_alert_looks_only_at_the_target_weeks_claim(job: Job) -> None:
    # D13 (#2): the ADR's "the unfinished claim" (:121) is the target week's. Another week's claim,
    # left unfinished for hours, is no reason to call this week's lock stuck.
    _seed_week(job, "approved")
    _seed_claim(job, PRIOR, started=_utc_text(SUN_10 - timedelta(hours=3)))
    before = _snapshot(job)

    with _held(job.lock_dir / "job-cart_fill.lock"):
        _run(job, "cart_fill", SUN_10, W)

    assert job.telegram.attempts == []
    assert _snapshot(job) == before


@pytest.mark.parametrize(
    ("week", "now", "refused"),
    [
        pytest.param(date(2026, 8, 2), SUN_10, True, id="eight-weeks-old"),
        pytest.param(PRIOR, SUN_10, True, id="seven-days-old"),
        pytest.param(PRIOR, _local(2026, 9, 26, 10), False, id="six-days-old"),
    ],
)
def test_retry_cart_refuses_a_week_past_the_six_day_horizon(
    job: Job, week: date, now: datetime, refused: bool
) -> None:
    # D14 (#5): "retry cart" mustn't fill an old list. The horizon is reconcile's, today − 6 days.
    _seed_week(job, "approved", week)
    _seed_claim(job, week, outcome="interrupted", detail="Meijer asked for a login")
    before = _snapshot(job)

    _run(job, "cart_fill", now, week, retry=True)

    message = _one_message(job)
    if refused:
        assert "too old" in message.lower()
        assert job.filler.calls == []
        assert _snapshot(job) == before
    else:
        assert len(job.filler.calls) == 1


def test_an_unreadable_week_doesnt_stop_reconcile_reaching_the_next(
    job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    # D15 (#6): reconcile reads each week inside its own try; the unreadable one is logged.
    caplog.set_level(logging.DEBUG)
    job.db.execute(
        "INSERT INTO weekly_plan (week_start, custody, components, status)"
        " VALUES (?, 'wed+sat_sun', '{\"not\": \"a proposal\"}', 'approved')",
        (PRIOR.isoformat(),),
    )
    job.db.commit()
    _seed_week(job, "approved")

    _run(job, "reconcile", _local(2026, 9, 26, 18, 15))

    assert job.spawner.calls == [WEEK_ARGS]
    assert _week(job)["mealie_plan_ref"] == "fake-plan-2026-09-27"
    assert "2026-09-20" in caplog.text, "the unreadable week must be logged"


def test_an_undelivered_expiry_is_resent_as_the_expiry_not_as_a_partway_notice(job: Job) -> None:
    # D17 (#9): the expiry message failed to send, so its claim is unfinished with detail
    # "expired". No fill ran, so the partway advice would be wrong.
    _seed_week(job, "approved")
    _seed_claim(job, detail="expired")

    _run(job, "cart_fill", _local(2026, 9, 28, 1, 16), W)

    message = _one_message(job)
    assert "never filled" in message and "retry cart" in message
    assert "stopped partway" not in message
    assert job.filler.calls == []
    assert _claim(job)["outcome"] == "interrupted"


def test_a_sigterm_just_after_the_claim_commits_still_fails_our_claim(
    job: Job, monkeypatch: pytest.MonkeyPatch, signal_guard: SignalGuard
) -> None:
    # D18 (#10): "our claim" is decided by re-reading, not by an in-memory flag, so a signal between
    # the claim's commit and the flag still gets detail, one message and `failed`. The next tick
    # then has nothing to say.
    real_claim = plan_state.claim

    def claim_then_sigterm(conn: sqlite3.Connection, name: str, week_start: date) -> str:
        state = real_claim(conn, name, week_start)
        os.kill(os.getpid(), signal.SIGTERM)
        return state

    for module in (plan_state, jobs):
        for attribute, value in list(vars(module).items()):
            if value is real_claim:
                monkeypatch.setattr(module, attribute, claim_then_sigterm)
    _seed_week(job, "approved")

    _run(job, "cart_fill", SUN_10, W)
    _run(job, "cart_fill", SUN_10_16, W)

    assert signal_guard.hits == []
    claim = _claim(job)
    assert claim["outcome"] == "failed" and claim["detail"]
    assert job.filler.calls == []
    assert f"job cart_fill failed: {claim['detail']}" in _one_message(job)


@pytest.mark.parametrize("path", ["stale-alert", "busy-retry", "inspect-notice"])
def test_a_message_sent_outside_the_act_step_leaves_a_log_line(
    job: Job, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    # Review #16: jobs.log is the record (ADR), so the stale alert, the busy --retry reply and
    # Inspect's interrupted notice each log a line on the meals logger.
    _seed_week(job, "approved")
    _seed_claim(
        job, PRIOR if path == "busy-retry" else W, started=_utc_text(SUN_10 - timedelta(minutes=46))
    )
    caplog.set_level(logging.INFO)
    lock = job.lock_dir / "job-cart_fill.lock"

    with _held(lock) if path != "inspect-notice" else nullcontext():
        _run(job, "cart_fill", SUN_10, W, retry=path == "busy-retry")

    _one_message(job)
    lines = [
        r for r in caplog.records if r.name.split(".")[0] == "meals" and r.levelno >= logging.INFO
    ]
    assert lines, f"the {path} message left no line in jobs.log"


# ── Codex-sweep repairs (seam map D19, D20) ──────────────────────────────────


def test_reconcile_redelivers_the_report_of_a_week_ordered_before_it_arrived(job: Job) -> None:
    # D19 (C1): the report committed, Telegram failed, then the bot marked the week ordered.
    # Reconcile still spawns the fill, whose Inspect re-sends the stored report without filling.
    _seed_week(job, "ordered", ref="plan-27")
    _seed_claim(job, detail=REPORT.model_dump_json())

    _run(job, "reconcile", SUN_10_15)

    assert job.spawner.calls == [WEEK_ARGS]
    _run_spawned(job, SUN_10_16)
    assert job.filler.calls == []
    assert "tahini" in _one_message(job)
    assert _claim(job)["outcome"] == "done"


class MealieNotConfigured:
    """__main__'s stand-in when MEALIE_TOKEN is unset (D20): every call raises."""

    def _refuse(self, *args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("MEALIE_TOKEN is not set")

    import_url = get_recipe = list_by_tag = set_meal_plan = _refuse


def test_reconcile_restarts_fills_even_when_mealie_cant_be_used(
    job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    # D20 (C2): Mealie's configuration mustn't block cart recovery; the publish failure is logged.
    caplog.set_level(logging.DEBUG)
    job = replace(job, deps=replace(job.deps, mealie=MealieNotConfigured()))
    _seed_week(job, "approved")

    _run(job, "reconcile", SUN_10_15)

    assert job.spawner.calls == [WEEK_ARGS]
    assert _week(job)["mealie_plan_ref"] is None
    assert "MEALIE_TOKEN" in caplog.text


# ── ultrareview repairs (seam map D23, D26, D27) ─────────────────────────────


def test_a_sigterm_during_the_report_redelivery_leaves_the_filled_cart_alone(
    job: Job, signal_guard: SignalGuard
) -> None:
    # D23 (U1): the catch-all looks at the target week's claim whatever the ownership. An unfinished
    # claim whose effect is recorded only gets a log line: no "empty the cart" for a filled cart,
    # and the committed report stays for the next tick.
    _seed_week(job, "cart_filled", ref="plan-27")
    _seed_claim(job, detail=REPORT.model_dump_json())
    committed = _claim(job)["detail"]
    job.telegram.on_send = _sigterm_on_first_send()

    _run(job, "cart_fill", SUN_10_16, W)

    assert signal_guard.hits == []
    assert job.telegram.delivered == [], "the catch-all only logs over a committed result"
    assert (_claim(job)["outcome"], _claim(job)["detail"]) == (None, committed)
    job.telegram.on_send = None

    _run(job, "cart_fill", _local(2026, 9, 27, 11, 16), W)

    assert job.filler.calls == []
    assert "tahini" in _one_message(job)
    assert (_claim(job)["outcome"], _claim(job)["detail"]) == ("done", committed)


def test_a_retry_logs_the_prior_claim_before_resetting_it(
    job: Job, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # D26 (U4): ADR :179, "write the prior row to data/logs/jobs.log", then reset. Logged after the
    # reset, a signal between the two would lose the prior diagnostics.
    caplog.set_level(logging.INFO)
    real_reset = plan_state.reset_for_retry
    log_at_reset: list[str] = []

    def reset_spy(conn: sqlite3.Connection, name: str, week_start: date) -> plan_state.JobRun:
        log_at_reset.append(caplog.text)
        return real_reset(conn, name, week_start)

    for module in (plan_state, jobs):
        for attribute, value in list(vars(module).items()):
            if value is real_reset:
                monkeypatch.setattr(module, attribute, reset_spy)
    _seed_week(job, "approved")
    _seed_claim(job, outcome="interrupted", detail="Meijer asked for a login")

    _run(job, "cart_fill", SUN_10, W, retry=True)

    assert len(log_at_reset) == 1
    assert "Meijer asked for a login" in log_at_reset[0], "the prior claim is logged before reset"
    assert len(job.filler.calls) == 1


@pytest.mark.parametrize("path", ["fill", "redelivery"])
@pytest.mark.parametrize(("subtotal_cents", "warns"), [(3499, True), (3500, False)])
def test_a_cart_under_35_dollars_warns_about_the_pickup_fee(
    job: Job, path: str, subtotal_cents: int, warns: bool
) -> None:
    # D27 (U5): PLAN :26 ("warns if the order is under $35"), :668, :688 ($4.95 pickup under $35),
    # on the first report and on its redelivery.
    report = REPORT.model_copy(update={"subtotal_cents": subtotal_cents})
    if path == "fill":
        _seed_week(job, "approved")
        job.filler.report = report
    else:
        _seed_week(job, "cart_filled", ref="plan-27")
        _seed_claim(job, detail=report.model_dump_json())

    _run(job, "cart_fill", SUN_10, W)

    assert ("under $35" in _one_message(job)) is warns
