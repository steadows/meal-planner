"""The bot's Application and entry point (PLAN.md, Phase 4; ADR-0001, Decision).

`run_bot()` refuses missing or rejected tokens, missing, zero or group chat ids, and a PANTRY_DB
that cannot open and migrate. Its chat lock fails closed. It checks the token with one getMe,
then polls for messages and edits only, keeping any pending updates. `/pantry` opens the pantry
once per command, never creates the file, and replies in plain text.
"""

import asyncio
import logging
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
    TypeHandler,
)
from telegram.request import BaseRequest

from meals import background
from meals.bot.gate import gate
from meals.config import get_settings
from meals.contracts import Pantry, PantryItem
from meals.db import get_db
from meals.pantry import SqlitePantry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotDeps:
    """The allowed chat and a fresh pantry context for each command."""

    allowed_chat_id: int
    open_pantry: Callable[[], AbstractContextManager[Pantry]]


def build_app(
    token: str, deps: BotDeps, *, request: BaseRequest | None = None
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Build sequential dispatch with a blocking gate before /pantry and optional fake HTTP."""
    builder = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(False)
    )
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()
    app.add_error_handler(_stop_on_error, block=True)
    app.add_handler(
        TypeHandler(Update, partial(gate, allowed_chat_id=deps.allowed_chat_id), block=True),
        group=-1,
    )
    app.add_handler(CommandHandler("pantry", partial(_pantry, deps=deps)), group=0)
    return app


async def _stop_on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log a handler failure and stop dispatch without replying to the chat."""
    logger.error("Stopping update after handler failure", exc_info=context.error)
    raise ApplicationHandlerStop


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


async def _check_token(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Verify with initialize's single getMe, closing the HTTP clients before polling."""
    try:
        async with app.bot:
            pass
    except InvalidToken:
        raise RuntimeError("Telegram rejected TELEGRAM_BOT_TOKEN") from None


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
    except Exception as exc:
        raise RuntimeError(f"Could not open PANTRY_DB: {path}") from exc

    @contextmanager
    def open_pantry() -> Iterator[Pantry]:
        if not path.is_file():
            raise RuntimeError(f"PANTRY_DB does not exist: {path}")
        with closing(get_db(path)) as conn:
            yield SqlitePantry(conn)

    deps = BotDeps(allowed_chat_id=settings.telegram_allowed_chat_id, open_pantry=open_pantry)
    app = build_app(settings.telegram_bot_token.get_secret_value(), deps)
    asyncio.run(_check_token(app))
    try:
        app.run_polling(allowed_updates=["message", "edited_message"])
    except InvalidToken:
        raise RuntimeError("Telegram rejected TELEGRAM_BOT_TOKEN") from None
