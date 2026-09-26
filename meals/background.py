"""Background work for the bot: chat-sized work off the event loop, and detached job processes.

ADR-0001 (docs/adr/ADR-0001-runtime-model.md), Decision and Ownership.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_IN_FLIGHT = 2
DEADLINE_S = 15 * 60
ERROR_MAX_CHARS = 300
BUSY_TEXT = "I'm already working on two requests. Try again in a few minutes."
TIMEOUT_TEXT = "That took over 15 minutes, so I gave up. Try again later."

_in_flight = 0


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    work: Callable[[], T],
    render: Callable[[T], str],
    ack: str,
) -> None:
    """Reply `ack`, run `work` in a worker thread, then reply `render(result)`. Returns at once.

    At most MAX_IN_FLIGHT requests run at a time; beyond that the reply is BUSY_TEXT and `work`
    never runs. After DEADLINE_S the reply is TIMEOUT_TEXT, but the thread can't be cancelled, so
    it keeps its slot until it really ends (ADR-0001, Consequences). **`work` must therefore have
    a bounded runtime** (claude_runner's timeout, httpx's timeouts, SQLite's busy timeout): a
    worker that never returns holds its slot until the bot restarts. Every reply is plain text.
    """
    raise NotImplementedError


def spawn_job(name: str, *args: str) -> int:
    raise NotImplementedError
