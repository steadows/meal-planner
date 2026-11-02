"""Chat lock and committed update claims (PLAN.md Phase 4; ADR-0001, Ownership).

`build_app` registers `gate` as a blocking handler in group -1, so it runs before every other
handler. It passes only an ordinary message or edit from TELEGRAM_ALLOWED_CHAT_ID. Business
messages, messages sent through a business connection and every other update kind stop here.
Logging failures do not bypass the lock. Allowed updates are claimed once before dispatch;
expired claims are pruned in the same transaction, which always ends before returning.
"""

import logging
import sqlite3

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)


def claim(conn: sqlite3.Connection, update_id: int) -> bool:
    """Prune then claim an exact integer id, committing replays too; roll back any failure."""
    with conn:
        if type(update_id) is not int:
            raise TypeError("update_id must be an int")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM telegram_update WHERE seen_at < datetime('now', '-48 hours')")
        inserted = conn.execute(
            "INSERT INTO telegram_update(update_id) VALUES (?) ON CONFLICT(update_id) DO NOTHING",
            (update_id,),
        ).rowcount
    return inserted == 1


async def gate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    allowed_chat_id: int,
    gate_conn: sqlite3.Connection,
) -> None:
    """Stop unsupported, business or foreign-chat updates before any later handler."""
    message = update.message or update.edited_message
    if (
        message is None
        or message.chat.id != allowed_chat_id
        or message.business_connection_id is not None
    ):
        try:
            logger.info(
                "dropped update from chat %s", "none" if message is None else message.chat.id
            )
        finally:
            raise ApplicationHandlerStop
    if not claim(gate_conn, update.update_id):
        try:
            logger.info("dropped replayed update %s", update.update_id)
        finally:
            raise ApplicationHandlerStop
