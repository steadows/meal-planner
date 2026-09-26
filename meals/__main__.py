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

from meals import background, jobs, planner
from meals.config import PROJECT_ROOT, get_settings
from meals.contracts import CartReport, Custody, WeekProposal
from meals.db import get_db
from meals.mealie_client import HttpMealieClient
from meals.pantry import SqlitePantry

logger = logging.getLogger("meals")

JOB_LOG = PROJECT_ROOT / "data" / "logs" / "jobs.log"
_SUNDAY = 6


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
    send = jobs.TelegramSend(token.get_secret_value(), chat_id)
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
    try:
        day = date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from exc
    if day.weekday() != _SUNDAY:
        raise argparse.ArgumentTypeError(f"{text} isn't a Sunday; a week is named by its Sunday")
    return day


def _configure_logging() -> None:
    """jobs.log is the record (ADR-0001); stderr is what launchd captures."""
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.FileHandler(JOB_LOG, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _deps(conn: sqlite3.Connection, send: jobs.TelegramSend, lock_dir: Path) -> jobs.Deps:
    pantry = SqlitePantry(conn)
    mealie = HttpMealieClient.from_settings()

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


def _report_startup_failure(send: jobs.TelegramSend, name: str, exc: Exception) -> int:
    """The job couldn't even start (a bad path, a missing token): say so, loudly."""
    logger.exception("job %s couldn't start", name)
    try:
        first_line = (str(exc).strip().splitlines() or [""])[0]
        send(f"job {name} failed to start: {type(exc).__name__}: {first_line}"[:500])
    except jobs.DeliveryFailed:
        logger.error("couldn't send the start-up failure for job %s", name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
