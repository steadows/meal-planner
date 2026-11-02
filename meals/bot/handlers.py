"""The bot's Application and entry point (PLAN.md, Phase 4; ADR-0001, Decision).

`run_bot()` refuses missing or rejected tokens, missing, zero or group chat ids, and a PANTRY_DB
that cannot open and migrate. Its chat lock fails closed; its error log holds exception types and
code locations only.
It checks the token with one getMe, then polls for messages and edits only, keeping any pending
updates. `/pantry` checks that the file exists, opens it once per command, and replies in plain text.
Free text takes an arrival ticket and deadline before background parsing and guarded pantry writes.
Edited messages ask for a new message. Parse failures expose only the runner's safe reason.
"""

import asyncio
import logging
import sqlite3
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

from telegram import Update
from telegram.error import InvalidToken
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import BaseRequest

from meals import background
from meals.bot import intents, pantry_ops
from meals.bot.gate import gate
from meals.config import get_settings
from meals.contracts import ClaudeRunnerError, Pantry, PantryItem
from meals.db import get_db
from meals.pantry import SqlitePantry

logger = logging.getLogger(__name__)

APPLY_MARGIN_S = 10.0


@dataclass(frozen=True)
class BotDeps:
    """The allowed chat, event-loop claim connection, and fresh pantry context per call."""

    allowed_chat_id: int
    open_pantry: Callable[[], AbstractContextManager[Pantry]]
    gate_conn: sqlite3.Connection


def build_app(
    token: str, deps: BotDeps, *, request: BaseRequest | None = None
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Build sequential, gated dispatch with per-Application write ordering and optional fake HTTP."""
    builder = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(False)
    )
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()
    latest = pantry_ops.LatestWrites()
    app.add_error_handler(_stop_on_error, block=True)
    app.add_handler(
        TypeHandler(
            Update,
            partial(gate, allowed_chat_id=deps.allowed_chat_id, gate_conn=deps.gate_conn),
            block=True,
        ),
        group=-1,
    )
    app.add_handler(
        CommandHandler("pantry", partial(_pantry, deps=deps), filters=filters.UpdateType.MESSAGE)
    )
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, _edited_text))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE,
            partial(_free_text, deps=deps, latest=latest),
        )
    )
    return app


async def _stop_on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log a handler failure and stop dispatch without replying to the chat."""
    try:
        logger.error(
            "Stopping update after handler failure\n%s",
            _format_error(context.error),
        )
    finally:
        raise ApplicationHandlerStop


def _format_error(error: BaseException | None) -> str:
    """Render the visible exception chain with only stack frames and type names."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        chain.append(error)
        seen.add(id(error))
        if error.__cause__ is not None:
            error = error.__cause__
        else:
            error = None if error.__suppress_context__ else error.__context__
    lines: list[str] = []
    for exc in reversed(chain):
        lines.extend(traceback.format_tb(exc.__traceback__))
        lines.append(f"{type(exc).__name__}\n")
    return "".join(lines)


async def _pantry(update: Update, context: ContextTypes.DEFAULT_TYPE, *, deps: BotDeps) -> None:
    """Read once on the event loop, close the pantry, then send plain-text chunks."""
    message = update.effective_message
    if message is None:
        return
    try:
        with deps.open_pantry() as pantry:
            items = pantry.list_items()
    except Exception as exc:
        logger.exception("couldn't read the pantry")
        text = ("Couldn't read the pantry: " + background.first_line(exc))[
            : background.ERROR_MAX_CHARS
        ]
    else:
        text = _pantry_text(items)
    for chunk in background.split_message(text):
        await message.reply_text(chunk, parse_mode=None)


def _pantry_text(items: tuple[PantryItem, ...]) -> str:
    """List each status in pantry order, with an explicit empty state."""
    if not items:
        return "The pantry is empty. Run the seed loader first."
    to_buy = ", ".join(item.name for item in items if item.status == "buy_next_time") or "nothing"
    have = ", ".join(item.name for item in items if item.status == "have") or "nothing"
    return f"On the list: {to_buy}\nHave: {have}"


async def _edited_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An edit never reruns a command or parse; ask for a fresh message."""
    if update.edited_message is not None:
        await update.edited_message.reply_text(
            "Please send that again as a new message.", parse_mode=None
        )


async def _free_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    deps: BotDeps,
    latest: pantry_ops.LatestWrites,
) -> None:
    """Capture arrival order and the apply deadline on the event loop before queuing work."""
    message = update.message
    if message is None or message.text is None:
        return
    ticket = latest.ticket()
    apply_by = time.monotonic() + background.DEADLINE_S - APPLY_MARGIN_S
    work = partial(
        _text_work, message.text, deps=deps, latest=latest, ticket=ticket, apply_by=apply_by
    )
    await background.start(update, context, work, str, "On it.")


def _text_work(
    text: str, *, deps: BotDeps, latest: pantry_ops.LatestWrites, ticket: int, apply_by: float
) -> str:
    """Open the pantry in the worker, parse once, and keep parse failures out of background logs."""
    try:
        with deps.open_pantry() as pantry:
            parsed = intents.parse(text, items=pantry.list_items())
            lines = pantry_ops.apply(
                parsed.intents, pantry=pantry, ticket=ticket, apply_by=apply_by, latest=latest
            )
    except ClaudeRunnerError as exc:
        reason = exc.reason
    else:
        asked = tuple(f'What did you mean by "{_single_line(part)}"?' for part in parsed.unclear)
        return (
            "\n".join((*lines, *asked)) or "I couldn't understand that. Please say it another way."
        )
    # Outside the except block: even a logging failure must not chain the parse error's output.
    logger.warning("couldn't parse the message: %s", reason)
    return ("Couldn't understand that: " + _single_line(reason))[: background.ERROR_MAX_CHARS]


def _single_line(text: str) -> str:
    """`text` with its line breaks turned into spaces, so one reply line stays one line."""
    return " ".join(text.splitlines())


async def _check_token(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Verify with initialize's single getMe, closing the HTTP clients before polling."""
    try:
        async with app.bot:
            pass
    except InvalidToken:
        raise RuntimeError("Telegram rejected TELEGRAM_BOT_TOKEN") from None
    except Exception as exc:
        raise RuntimeError(f"Telegram getMe failed ({type(exc).__name__})") from None


def run_bot() -> None:
    """Validate startup settings, then poll with a fresh SQLite connection per pantry read."""
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not settings.telegram_allowed_chat_id:
        raise RuntimeError("TELEGRAM_ALLOWED_CHAT_ID is missing")
    if settings.telegram_allowed_chat_id < 0:
        raise RuntimeError("TELEGRAM_ALLOWED_CHAT_ID must be a private chat id")
    path = settings.pantry_db
    if not path.is_file():
        raise RuntimeError(f"PANTRY_DB does not exist: {path}")
    try:
        with closing(get_db(path)):
            pass
        gate_conn = get_db(path)
    except Exception as exc:
        raise RuntimeError(f"Could not open PANTRY_DB: {path}") from exc

    @contextmanager
    def open_pantry() -> Iterator[Pantry]:
        if not path.is_file():
            raise RuntimeError(f"PANTRY_DB does not exist: {path}")
        with closing(get_db(path)) as conn:
            yield SqlitePantry(conn)

    with closing(gate_conn):
        deps = BotDeps(
            allowed_chat_id=settings.telegram_allowed_chat_id,
            open_pantry=open_pantry,
            gate_conn=gate_conn,
        )
        app = build_app(settings.telegram_bot_token.get_secret_value(), deps)
        _poll(app)


def _poll(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Check the token with one getMe, then poll; any failure becomes a type-only refusal."""
    asyncio.run(_check_token(app))
    try:
        app.run_polling(allowed_updates=["message", "edited_message"])
    except InvalidToken:
        raise RuntimeError("Telegram rejected TELEGRAM_BOT_TOKEN") from None
    except Exception as exc:
        raise RuntimeError(f"Telegram polling failed ({type(exc).__name__})") from None
