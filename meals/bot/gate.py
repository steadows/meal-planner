"""The bot's chat lock (PLAN.md, Phase 4): only Steve's chat reaches a handler.

`build_app` registers `gate` as a blocking handler in group -1, so it runs before every other
handler. It passes only an ordinary message or edit from TELEGRAM_ALLOWED_CHAT_ID. Business
messages, messages sent through a business connection and every other update kind stop here.
"""

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE, *, allowed_chat_id: int) -> None:
    """Stop unsupported, business or foreign-chat updates before any later handler."""
    message = update.message or update.edited_message
    if (
        message is None
        or message.chat.id != allowed_chat_id
        or message.business_connection_id is not None
    ):
        logger.info("dropped update from chat %s", "none" if message is None else message.chat.id)
        raise ApplicationHandlerStop
