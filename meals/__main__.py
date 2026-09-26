"""`python -m meals job <name> [--now ISO] [--week YYYY-MM-DD] [--retry]`: the composition root
(ADR-0001). It wires the real modules into `jobs.Deps` and runs one job.

The `bot` subcommand comes with the bot lane's entry point (plan task 4.1). `cart_fill` refuses to
fill until the cart lane merges: ADR-0001 ships it only after `cart.fill(hold_fds)` and the P4.4
lock-retention test.
"""

import argparse
import logging
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NoReturn

from pydantic import TypeAdapter, ValidationError

from meals import background, jobs, planner
from meals.config import PROJECT_ROOT, get_settings
from meals.contracts import (
    CartReport,
    Custody,
    MealieClient,
    RecipeOption,
    SundayDate,
    WeekProposal,
)
from meals.db import get_db
from meals.mealie_client import HttpMealieClient
from meals.pantry import SqlitePantry

logger = logging.getLogger("meals")

JOB_LOG = PROJECT_ROOT / "data" / "logs" / "jobs.log"
_SUNDAY_DATE: TypeAdapter[date] = TypeAdapter(SundayDate)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.name == "cart_fill" and args.week is None:
        parser.error("cart_fill needs --week")
    _configure_logging()
    settings = get_settings()
    token, chat_id = settings.telegram_bot_token, settings.telegram_allowed_chat_id
    if token is None or chat_id is None:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID must be set in .env")
        return 2
    send = background.TelegramSend(token.get_secret_value(), chat_id)
    try:
        conn = get_db()
    except Exception as exc:
        return _report_startup_failure(send, args.name, exc)
    try:
        deps = _deps(conn, send, settings.claude_lock_dir)
    except Exception as exc:
        conn.close()
        return _report_startup_failure(send, args.name, exc)
    try:
        now = args.now or datetime.now(UTC)
        return jobs.run_job(args.name, deps, now=now, week=args.week, retry=args.retry)
    except (Exception, jobs.JobTimeout, jobs.JobTerminated) as exc:
        # run_job reports its own failures; this is its catch-all or lock handling failing
        # (a locked database, a signal outside the catch-all). Always report (ADR-0001, step 7).
        return _report(send, args.name, exc, "failed")
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m meals")
    commands = parser.add_subparsers(dest="command", required=True)
    job = commands.add_parser("job", help="run one scheduled job")
    job.add_argument("name", choices=jobs.JOBS)
    job.add_argument("--now", type=_now, help="ISO time; a naive one is Detroit time")
    job.add_argument("--week", type=_sunday, help="the week's Sunday, YYYY-MM-DD")
    job.add_argument("--retry", action="store_true", help="retry an interrupted or failed run")
    return parser


def _now(text: str) -> datetime:
    """An ISO time; a naive one is America/Detroit wall-clock time."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO time: {text!r}") from exc
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=jobs.TZ)


def _sunday(text: str) -> date:
    """A week is named by its Sunday (contracts.SundayDate)."""
    try:
        return _SUNDAY_DATE.validate_python(text)
    except ValidationError as exc:
        raise argparse.ArgumentTypeError(f"{text!r}: {exc.errors()[0]['msg']}") from exc


def _configure_logging() -> None:
    """jobs.log is the record (ADR-0001); stderr is what launchd captures."""
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.FileHandler(JOB_LOG, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _deps(conn: sqlite3.Connection, send: background.TelegramSend, lock_dir: Path) -> jobs.Deps:
    pantry = SqlitePantry(conn)
    mealie: MealieClient
    try:
        mealie = HttpMealieClient.from_settings()
    except RuntimeError as exc:  # MEALIE_TOKEN missing or malformed (seam map D20)
        logger.warning("Mealie isn't configured (%s); jobs that need it will fail", exc)
        mealie = _MealieNotConfigured(str(exc))

    def propose(week: date, custody: Custody, recent: Sequence[WeekProposal]) -> WeekProposal:
        return planner.propose(week, custody, recent=recent, pantry=pantry, mealie=mealie)

    return jobs.Deps(
        conn=conn,
        lock_dir=lock_dir,
        send=send,
        spawn=background.spawn_job,
        propose=propose,
        fill=_cart_not_wired,
        mealie=mealie,
    )


def _cart_not_wired(plan: WeekProposal, hold_fds: tuple[int, ...]) -> CartReport:
    raise RuntimeError(
        "cart_fill isn't wired until the cart lane merges (ADR-0001: cart_fill ships only after "
        "cart.fill(hold_fds) and the P4.4 lock test)"
    )


class _MealieNotConfigured:
    """`deps.mealie` when MEALIE_TOKEN is missing or malformed. Jobs that never reach Mealie (the
    nudge, redeliveries, reconcile's cart recovery) still run; any call raises the problem."""

    def __init__(self, problem: str) -> None:
        self._problem = problem

    def _fail(self) -> NoReturn:
        raise RuntimeError(self._problem)

    def import_url(self, url: str) -> str:
        self._fail()

    def get_recipe(self, slug: str) -> RecipeOption:
        self._fail()

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        self._fail()

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        self._fail()


def _report_startup_failure(send: background.TelegramSend, name: str, exc: Exception) -> int:
    """The job couldn't even start (a bad path, a database that won't open)."""
    return _report(send, name, exc, "failed to start")


def _report(send: background.TelegramSend, name: str, exc: BaseException, what: str) -> int:
    """Log the failure to jobs.log and tell Steve, loudly, then exit 1."""
    reason = background.first_line(exc)
    logger.error("job %s %s: %s", name, what, reason, exc_info=jobs.safe_exc_info(exc))
    try:
        send(f"job {name} {what}: {reason}"[: jobs.REASON_MAX_CHARS])
    except background.DeliveryFailed:
        logger.error("couldn't tell Steve that job %s %s", name, what)
    return 1


if __name__ == "__main__":
    sys.exit(main())
