"""The scheduled jobs (ADR-0001, Jobs and Job contract): sat_propose, sat_nudge, sun_autoapprove,
cart_fill and reconcile. Each runs as its own short `python -m meals job <name>` process.

Every job follows the same contract:
1. Lock: a non-blocking flock on `<lock_dir>/job-<name>.lock`, released by closing the fd.
2. Inspect: a claim a dead run left unfinished is settled first.
3. Decide: the time window (America/Detroit, half-open) and preconditions. Not acting writes nothing.
4. Claim.
5. Act. The job's message is the last step.
6. Finish, and only after the message is delivered.
7. Always report: a catch-all for any failure, the per-job alarm and SIGTERM included.

reconcile runs steps 1, 5 and 7 only. Dependencies arrive in `Deps`, so tests use fakes and
`__main__` composes the real modules.
"""

import asyncio
import fcntl
import logging
import os
import signal
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from types import FrameType
from typing import Literal, get_args
from zoneinfo import ZoneInfo

import telegram
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError

from meals import plan_state
from meals.background import first_line, split_message
from meals.contracts import CartReport, Custody, MealieClient, WeekProposal
from meals.plan_state import APPROVED_OR_LATER, JobRun, StoredWeek

logger = logging.getLogger(__name__)

JobName = Literal["sat_propose", "sat_nudge", "sun_autoapprove", "cart_fill", "reconcile"]
JOBS: tuple[JobName, ...] = get_args(JobName)

TZ = ZoneInfo("America/Detroit")
ALARM_S = 45 * 60
SEND_ATTEMPTS = 3
SEND_BUDGET_S = 120
REASON_MAX_CHARS = 500
NUDGE_AFTER = timedelta(hours=3)
RECONCILE_DAYS = 6
DEFAULT_CUSTODY: Custody = "wed+sat_sun"  # the first week's guess (seam map D2)

_BACKOFF_S = (5.0, 15.0)
_SAT, _SUN = 5, 6
_FILLED = ("cart_filled", "ordered")
# Spelled out rather than strftime, which follows the process locale.
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class DeliveryFailed(Exception):
    """A Telegram message wasn't delivered: retries ran out, or Telegram refused it."""


class JobTimeout(BaseException):
    """SIGALRM: the job ran past ALARM_S. A BaseException, so no `except Exception` swallows it."""


class JobTerminated(BaseException):
    """SIGTERM (launchd stopping the job). A BaseException for the same reason."""


@dataclass(frozen=True)
class Deps:
    """What a job reaches the outside world through. `send` raises if the text wasn't delivered."""

    conn: sqlite3.Connection
    lock_dir: Path
    send: Callable[[str], None]
    spawn: Callable[..., int]
    propose: Callable[[date, Custody, Sequence[WeekProposal]], WeekProposal]
    fill: Callable[[WeekProposal, tuple[int, ...]], CartReport]
    mealie: MealieClient


@dataclass(frozen=True)
class _Run:
    name: JobName
    deps: Deps
    now: datetime  # America/Detroit
    week: date
    retry: bool
    lock_fd: int = -1

    @property
    def conn(self) -> sqlite3.Connection:
        return self.deps.conn

    @property
    def today(self) -> date:
        return self.now.date()


# ── entry point ──────────────────────────────────────────────────────────────


def run_job(
    name: JobName, deps: Deps, *, now: datetime, week: date | None = None, retry: bool = False
) -> int:
    """Run one job at `now` (timezone-aware) and return the process exit code."""
    if name not in JOBS:
        raise ValueError(f"unknown job {name!r}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if week is None and name == "cart_fill":
        raise ValueError("cart_fill needs an explicit week")
    if week is not None and week.weekday() != _SUN:
        raise ValueError(f"week {week} is not a Sunday")
    local = now.astimezone(TZ)
    run = _Run(name, deps, local, week or _target_week(local.date()), retry)
    with _signal_handlers(), _job_lock(deps.lock_dir, name) as lock_fd:
        if lock_fd is None:
            return _busy(run)
        return _guarded(replace(run, lock_fd=lock_fd))


def _target_week(today: date) -> date:
    """The Sunday on or after `today`: Saturday's jobs plan tomorrow's cook."""
    return today + timedelta(days=(_SUN - today.weekday()) % 7)


def _guarded(run: _Run) -> int:
    claimed = False
    try:
        if run.name == "reconcile":
            return _reconcile(run)
        settled = _inspect(run)
        if settled is not None:
            return settled
        act = _DECIDE[run.name](run)
        if act is None:
            return 0
        _start(run)
        claimed = True
        act()
        return 0
    except (Exception, JobTimeout, JobTerminated) as exc:
        return _catch_all(run, exc, claimed)


# ── the lock and the signals ─────────────────────────────────────────────────


@contextmanager
def _job_lock(lock_dir: Path, name: str) -> Iterator[int | None]:
    """Yield the fd holding this job's flock, or None if another process holds it. The lock is
    released by closing the fd, never with LOCK_UN, so a claude child holding a copy keeps it.
    Lock files are never deleted."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"job-{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            held = False
        yield fd if held else None
    finally:
        os.close(fd)


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    raise JobTimeout(f"gave up after {ALARM_S // 60} minutes")


def _on_term(signum: int, frame: FrameType | None) -> None:
    raise JobTerminated("stopped by SIGTERM")


@contextmanager
def _signal_handlers() -> Iterator[None]:
    """Route SIGALRM and SIGTERM into the catch-all, arm the alarm, and put everything back."""
    previous = {
        signal.SIGALRM: signal.signal(signal.SIGALRM, _on_alarm),
        signal.SIGTERM: signal.signal(signal.SIGTERM, _on_term),
    }
    signal.alarm(ALARM_S)
    try:
        yield
    finally:
        signal.alarm(0)
        for signum, handler in previous.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def _busy(run: _Run) -> int:
    """Another process holds the lock: say so only when Steve asked, or when it looks stuck."""
    if run.name == "reconcile":
        return 0
    holder = plan_state.running(run.conn, run.name)
    if run.retry:
        since = f" (since {_clock(holder.started_at)})" if holder is not None else ""
        _try_send(run, f"{run.name} is still running{since}. Try again once it has finished.")
    elif holder is not None and run.now - holder.started_at > timedelta(seconds=ALARM_S):
        _try_send(
            run,
            f"{run.name} has been running since {_clock(holder.started_at)}, longer than it "
            "should. It may be stuck: see deploy/README.md, Stuck run.",
        )
    return 0


# ── Inspect, claim, catch-all ────────────────────────────────────────────────


def _inspect(run: _Run) -> int | None:
    """Settle a claim a dead run left unfinished (this always ends the run), or return None to
    go on to Decide. An outcome already set ends the run too, unless this is a --retry."""
    claim = plan_state.get_run(run.conn, run.name, run.week)
    if claim is None:
        return None
    if claim.outcome is not None:
        return None if run.retry else 0
    if _effect_recorded(run):
        _REDELIVER[run.name](run, claim)
    else:
        _deliver(run, _NOTICES[run.name](run, claim.detail))
        plan_state.finish(run.conn, run.name, run.week, "interrupted")
    return 0


def _effect_recorded(run: _Run) -> bool:
    """Whether the job's effect is persisted, so only its message can be outstanding."""
    return _RECORDED[run.name](plan_state.get_week(run.conn, run.week))


def _start(run: _Run) -> None:
    """Claim the week, or with --retry reopen the interrupted or failed claim."""
    if run.retry:
        prior = plan_state.reset_for_retry(run.conn, run.name, run.week)
        logger.info("retrying %s for week %s; prior claim: %s", run.name, run.week, prior)
        return
    state = plan_state.claim(run.conn, run.name, run.week)
    if state != "claimed":
        raise plan_state.PlanStateError(f"{run.name} for week {run.week} is already {state}")


def _catch_all(run: _Run, exc: BaseException, claimed: bool) -> int:
    """Always report (ADR-0001, Job contract step 7)."""
    signal.alarm(0)
    reason = _reason(exc)
    logger.error("job %s for week %s failed: %s", run.name, run.week, reason, exc_info=exc)
    if isinstance(exc, DeliveryFailed):
        return 1  # nothing was delivered: the claim stays unfinished for the next tick
    if claimed and _effect_recorded(run):
        return 1  # never rewrite a committed result; Inspect redelivers it
    if claimed:
        plan_state.set_detail(run.conn, run.name, run.week, reason)
    if not _try_send(run, f"job {run.name} failed: {reason}"):
        return 1
    if claimed:
        plan_state.finish(run.conn, run.name, run.week, "failed")
    return 1


def _reason(exc: BaseException) -> str:
    return first_line(exc)[:REASON_MAX_CHARS]


def _deliver(run: _Run, text: str) -> None:
    run.deps.send(text)


def _try_send(run: _Run, text: str) -> bool:
    try:
        _deliver(run, text)
    except DeliveryFailed as exc:
        logger.error("couldn't send a %s message: %s", run.name, exc)
        return False
    return True


def _fail_quietly(run: _Run, reason: str) -> None:
    """Finish a claim `failed` with its reason, sending nothing."""
    plan_state.set_detail(run.conn, run.name, run.week, reason)
    plan_state.finish(run.conn, run.name, run.week, "failed")


def _refuse(run: _Run, reason: str) -> None:
    """A --retry that won't act: one short message, and nothing written (seam map D7)."""
    _deliver(run, f"Can't retry {run.name} for the week of {_day(run.week)}: {reason}.")


def _retry_refusal(run: _Run) -> str | None:
    claim = plan_state.get_run(run.conn, run.name, run.week)
    if claim is None or claim.outcome not in ("interrupted", "failed"):
        return "nothing to retry"
    return None


# ── sat_propose ──────────────────────────────────────────────────────────────


def _propose_window(local: datetime) -> bool:
    return local.weekday() == _SAT and dt_time(8) <= local.time() < dt_time(20)


def _decide_propose(run: _Run) -> Callable[[], None] | None:
    stored = plan_state.get_week(run.conn, run.week)
    if run.retry:
        refusal = _retry_refusal(run)
        if refusal is None and not _propose_window(run.now):
            refusal = "outside the Saturday window (8 am to 8 pm)"
        if refusal is None and stored is not None and stored.status != "proposed":
            refusal = "the week is already approved"
        if refusal is not None:
            _refuse(run, refusal)
            return None
        if stored is not None:
            proposal = stored.proposal
            return lambda: _send_proposal(run, proposal)  # "resend plan": no new planner run
        return lambda: _propose(run)
    if not _propose_window(run.now) or stored is not None:
        return None
    return lambda: _propose(run)


def _propose(run: _Run) -> None:
    latest = plan_state.latest_week(run.conn, run.week)
    custody = latest.custody if latest is not None and latest.custody else DEFAULT_CUSTODY
    recent = plan_state.recent_proposals(run.conn, run.week)
    proposal = run.deps.propose(run.week, custody, recent)
    if proposal.week_start != run.week:
        raise ValueError(f"the planner drafted week {proposal.week_start}, not {run.week}")
    if not plan_state.insert_week(run.conn, proposal, "proposed"):
        logger.warning("week %s was planned by another run meanwhile; not sending", run.week)
        plan_state.finish(run.conn, run.name, run.week, "done")  # seam map D6
        return
    _send_proposal(run, proposal)


def _send_proposal(run: _Run, proposal: WeekProposal) -> None:
    _deliver(run, _proposal_text(proposal))
    plan_state.finish(run.conn, run.name, run.week, "done")


def _redeliver_proposal(run: _Run, claim: JobRun) -> None:
    stored = plan_state.get_week(run.conn, run.week)
    if stored is not None and stored.status == "proposed" and _propose_window(run.now):
        _send_proposal(run, stored.proposal)
    else:
        _fail_quietly(run, "proposal not delivered")  # never sent outside its window


# ── sat_nudge ────────────────────────────────────────────────────────────────


def _nudge_window(local: datetime) -> bool:
    return (local.weekday() == _SAT and local.time() >= dt_time(16)) or (
        local.weekday() == _SUN and local.time() < dt_time(8)
    )


def _decide_nudge(run: _Run) -> Callable[[], None] | None:
    if run.retry:
        _refuse(run, "nothing to retry")
        return None
    if not _nudge_window(run.now):
        return None
    stored = plan_state.get_week(run.conn, run.week)
    proposed = plan_state.get_run(run.conn, "sat_propose", run.week)
    if stored is None or stored.status != "proposed" or proposed is None:
        return None
    finished = proposed.finished_at
    if proposed.outcome != "done" or finished is None or finished > run.now - NUDGE_AFTER:
        return None
    return lambda: _send_nudge(run)


def _send_nudge(run: _Run) -> None:
    _deliver(
        run,
        f"Reminder: the plan for the week of {_day(run.week)} is waiting for your picks. "
        "If there's no reply by Sunday 8 am, I'll reuse last week's plan.",
    )
    plan_state.finish(run.conn, run.name, run.week, "done")


def _redeliver_nudge(run: _Run, claim: JobRun) -> None:
    stored = plan_state.get_week(run.conn, run.week)
    if stored is None or stored.status != "proposed":
        plan_state.finish(run.conn, run.name, run.week, "done")  # nothing left to nudge about
    elif _nudge_window(run.now):
        _send_nudge(run)
    else:
        _fail_quietly(run, "nudge not delivered")


# ── sun_autoapprove ──────────────────────────────────────────────────────────


def _decide_autoapprove(run: _Run) -> Callable[[], None] | None:
    if run.retry:
        _refuse(run, "nothing to retry")
        return None
    if not (run.now.weekday() == _SUN and dt_time(8) <= run.now.time() < dt_time(20)):
        return None
    stored = plan_state.get_week(run.conn, run.week)
    if stored is not None and stored.status != "proposed":
        return None
    return lambda: _autoapprove(run)


def _autoapprove(run: _Run) -> None:
    last = plan_state.recent_proposals(run.conn, run.week, limit=1)
    if not last:
        plan_state.set_detail(run.conn, run.name, run.week, "no prior week")
        _deliver(
            run,
            f"There's no reply to the plan for the week of {_day(run.week)}, and no earlier "
            "week to reuse. Reply to Saturday's proposal to approve it.",
        )
        plan_state.finish(run.conn, run.name, run.week, "failed")
        return
    plan = last[0].model_copy(update={"week_start": run.week})
    if not _approve(run, plan):
        logger.warning("week %s was approved by another run meanwhile", run.week)
        plan_state.finish(run.conn, run.name, run.week, "done")
        return
    try:
        run.deps.spawn("cart_fill", "--week", run.week.isoformat())
        started = True
    except OSError as exc:
        logger.error("couldn't start cart_fill for week %s: %s", run.week, exc)
        started = False
    _deliver(run, _autoapproved_text(run, plan, started))
    plan_state.finish(run.conn, run.name, run.week, "done")


def _approve(run: _Run, plan: WeekProposal) -> bool:
    """Approve the week with `plan`: False if another run already moved it past `proposed`."""
    if plan_state.transition(run.conn, run.week, "proposed", "approved", proposal=plan):
        return True
    if plan_state.insert_week(run.conn, plan, "approved"):
        return True
    stored = plan_state.get_week(run.conn, run.week)  # lost a race: re-read
    if stored is not None and stored.status in APPROVED_OR_LATER:
        return False
    if plan_state.transition(run.conn, run.week, "proposed", "approved", proposal=plan):
        return True
    raise plan_state.PlanStateError(f"couldn't approve week {run.week}")


def _autoapproved_text(run: _Run, plan: WeekProposal, started: bool | None) -> str:
    proposed = plan_state.get_run(run.conn, "sat_propose", run.week)
    lines = [
        f"No reply to the plan for the week of {_day(run.week)}, so I'm reusing last week's: "
        + ", ".join(option.name for option in plan.recipe_options)
        + "."
    ]
    if proposed is None or proposed.outcome != "done":
        lines.append("Saturday's proposal didn't reach you.")
    if started is True:
        lines.append("I'm filling the Meijer cart now.")
    elif started is False:
        lines.append("I couldn't start the cart fill; I'll retry within the hour.")
    return "\n".join(lines)


def _redeliver_autoapprove(run: _Run, claim: JobRun) -> None:
    stored = plan_state.get_week(run.conn, run.week)
    if stored is None:
        raise plan_state.PlanStateError(f"week {run.week} vanished after it was approved")
    _deliver(run, _autoapproved_text(run, stored.proposal, None))
    plan_state.finish(run.conn, run.name, run.week, "done")


# ── cart_fill ────────────────────────────────────────────────────────────────


def _decide_fill(run: _Run) -> Callable[[], None] | None:
    stored = plan_state.get_week(run.conn, run.week)
    if run.retry:
        refusal = _retry_refusal(run)
        if refusal is None and stored is not None and stored.status in _FILLED:
            refusal = "the cart is already filled"
        if refusal is None and (stored is None or stored.status != "approved"):
            refusal = "the week isn't approved"
        if refusal is not None or stored is None:
            _refuse(run, refusal or "the week isn't approved")
            return None
        plan = stored.proposal
        return lambda: _fill(run, plan)  # a retry fills even an expired week
    if stored is None or stored.status != "approved":
        return None
    if run.week < run.today:
        return lambda: _expire(run)
    plan = stored.proposal
    return lambda: _fill(run, plan)


def _expire(run: _Run) -> None:
    plan_state.set_detail(run.conn, run.name, run.week, "expired")
    _deliver(
        run,
        f"The week of {_day(run.week)} was approved but its cart never filled. "
        "Reply 'retry cart' if you still want it.",
    )
    plan_state.finish(run.conn, run.name, run.week, "failed")


def _fill(run: _Run, plan: WeekProposal) -> None:
    report = run.deps.fill(plan, (run.lock_fd,))
    plan_state.record_cart(run.conn, run.week, report)
    _send_report(run, report)


def _send_report(run: _Run, report: CartReport) -> None:
    stored = plan_state.get_week(run.conn, run.week)
    published = stored is not None and stored.mealie_plan_ref is not None
    _deliver(run, _report_text(run.week, report, published))
    plan_state.finish(run.conn, run.name, run.week, "done")


def _redeliver_report(run: _Run, claim: JobRun) -> None:
    if claim.detail is None:
        raise plan_state.PlanStateError(f"cart_fill for week {run.week} has no stored report")
    _send_report(run, CartReport.model_validate_json(claim.detail))


# ── reconcile ────────────────────────────────────────────────────────────────


def _reconcile(run: _Run) -> int:
    """Publish approved plans to Mealie and restart or redeliver cart fills, week by week. It
    claims nothing, and one week's failure doesn't stop the others."""
    for stored in plan_state.weeks_since(run.conn, run.today - timedelta(days=RECONCILE_DAYS)):
        if stored.status in APPROVED_OR_LATER and stored.mealie_plan_ref is None:
            _publish(run, stored)
        if _needs_fill(run, stored):
            try:
                run.deps.spawn("cart_fill", "--week", stored.week_start.isoformat())
            except OSError as exc:
                logger.error("couldn't start cart_fill for week %s: %s", stored.week_start, exc)
    return 0


def _publish(run: _Run, stored: StoredWeek) -> None:
    mealie = run.deps.mealie
    try:
        slugs = [
            option.mealie_slug or mealie.import_url(option.url)
            for option in stored.proposal.recipe_options
        ]
        ref = mealie.set_meal_plan(stored.week_start, slugs)
    except Exception as exc:  # logged (the message only: Mealie's chained cause isn't vetted)
        logger.warning("publishing week %s to Mealie failed: %s", stored.week_start, exc)
        return
    if not plan_state.set_mealie_ref(run.conn, stored.week_start, ref):
        logger.warning(
            "week %s changed while it was published; ref %s not recorded", stored.week_start, ref
        )


def _needs_fill(run: _Run, stored: StoredWeek) -> bool:
    """Approved with no fill claim, or an unfinished one; or filled with its report undelivered
    (seam map D5)."""
    claim = plan_state.get_run(run.conn, "cart_fill", stored.week_start)
    unfinished = claim is not None and claim.outcome is None
    if stored.status == "approved":
        return claim is None or unfinished
    return stored.status == "cart_filled" and unfinished


# ── notices and message text ─────────────────────────────────────────────────


def _with_reason(text: str, reason: str | None) -> str:
    return f"{text} (Reason: {reason})" if reason else text


_NOTICES: dict[str, Callable[[_Run, str | None], str]] = {
    "cart_fill": lambda run, reason: _with_reason(
        "The cart fill stopped partway. The cart may already hold some or all items. "
        "Empty it in the Meijer app, then reply 'retry cart'.",
        reason,
    ),
    "sat_propose": lambda run, reason: _with_reason(
        "Saturday's proposal may not have gone out. Reply 'resend plan'.", reason
    ),
    "sun_autoapprove": lambda run, reason: _with_reason(
        f"Sunday's auto-approve stopped before it approved the week of {_day(run.week)}. "
        "Reply to Saturday's proposal to approve it.",
        reason,
    ),
}


def _proposal_text(proposal: WeekProposal) -> str:
    lines = [
        f"Plan for the week of {_day(proposal.week_start)} ({proposal.custody}, {proposal.mode})."
    ]
    if proposal.recipe_options:
        lines.append("Recipes (reply with the numbers you want):")
        for number, option in enumerate(proposal.recipe_options, start=1):
            minutes = f", {option.hands_on_min} min hands-on" if option.hands_on_min else ""
            lines.append(f"{number}. {option.name} ({option.source}{minutes}): {option.fit_note}")
    parts = proposal.components.model_dump()
    slots = [f"{slot}: {', '.join(items)}" for slot, items in parts.items() if items]
    if slots:
        lines.append("Components: " + "; ".join(slots))
    if proposal.lunch_builds:
        lines.append("Lunches: " + "; ".join(proposal.lunch_builds))
    if proposal.kid_nights:
        lines.append("Kid nights: " + "; ".join(proposal.kid_nights))
    if proposal.pantry_questions:
        lines.append("Still have enough of: " + ", ".join(proposal.pantry_questions) + "?")
    return "\n".join(lines)


def _report_text(week: date, report: CartReport, published: bool) -> str:
    lines = [f"Cart filled for the week of {_day(week)}."]
    if report.added:
        lines.append(f"Added ({len(report.added)}): " + ", ".join(report.added))
    if report.substituted:
        swaps = (f"{swap.wanted} → {swap.used}" for swap in report.substituted)
        lines.append("Substituted: " + ", ".join(swaps))
    if report.missing:
        lines.append("Couldn't find: " + ", ".join(report.missing))
    dollars, cents = divmod(report.subtotal_cents, 100)
    lines.append(f"Subtotal: ${dollars}.{cents:02d}")
    lines.append("Mealie plan: published" if published else "Mealie plan: pending, retrying hourly")
    return "\n".join(lines)


def _day(day: date) -> str:
    return f"{_DAYS[day.weekday()]} {_MONTHS[day.month - 1]} {day.day}"


def _clock(moment: datetime) -> str:
    local = moment.astimezone(TZ)
    return f"{_DAYS[local.weekday()]} {local:%H:%M}"


_DECIDE: dict[str, Callable[[_Run], Callable[[], None] | None]] = {
    "sat_propose": _decide_propose,
    "sat_nudge": _decide_nudge,
    "sun_autoapprove": _decide_autoapprove,
    "cart_fill": _decide_fill,
}
# What "the effect is recorded" means per job (ADR-0001, Job contract step 2).
_RECORDED: dict[str, Callable[[StoredWeek | None], bool]] = {
    "sat_propose": lambda stored: stored is not None,
    "sat_nudge": lambda stored: True,  # the message is the whole effect: the claim means it's owed
    "sun_autoapprove": lambda stored: stored is not None and stored.status in APPROVED_OR_LATER,
    "cart_fill": lambda stored: stored is not None and stored.status in _FILLED,
}
_REDELIVER: dict[str, Callable[[_Run, JobRun], None]] = {
    "sat_propose": _redeliver_proposal,
    "sat_nudge": _redeliver_nudge,
    "sun_autoapprove": _redeliver_autoapprove,
    "cart_fill": _redeliver_report,
}


# ── the Telegram sender ──────────────────────────────────────────────────────


class TelegramSend:
    """Deps.send for a job process: plain text to Steve's chat, split at Telegram's limit, with a
    fresh `telegram.Bot` per attempt. Transient errors (network, flood control) are retried up to
    SEND_ATTEMPTS within SEND_BUDGET_S; anything else, or running out, raises DeliveryFailed.

    DeliveryFailed carries only the error's type, never its text, and drops the chain: PTB's
    InvalidToken message includes the token."""

    def __init__(
        self,
        token: str,
        chat_id: int,
        *,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._sleep = sleep

    def __call__(self, text: str) -> None:
        pending = list(split_message(text))
        waited = 0.0
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                asyncio.run(self._send(pending))
                return
            except TelegramError as exc:
                wait = _retry_wait(exc, attempt)
                kind = type(exc).__name__
                if wait is None or attempt == SEND_ATTEMPTS or waited + wait > SEND_BUDGET_S:
                    raise DeliveryFailed(f"Telegram didn't take the message ({kind})") from None
                logger.warning("Telegram send attempt %d failed (%s); retrying", attempt, kind)
                self._sleep(wait)
                waited += wait

    async def _send(self, pending: list[str]) -> None:
        """Send the chunks in order, dropping each from `pending` once it's delivered, so a retry
        resumes where the last attempt stopped."""
        async with telegram.Bot(self._token) as bot:
            while pending:
                await bot.send_message(chat_id=self._chat_id, text=pending[0], parse_mode=None)
                pending.pop(0)


def _retry_wait(exc: TelegramError, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if the error isn't transient."""
    if isinstance(exc, RetryAfter):
        wait = exc.retry_after
        return wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
    if isinstance(exc, NetworkError) and not isinstance(exc, BadRequest):
        return _BACKOFF_S[min(attempt, len(_BACKOFF_S)) - 1]
    return None
