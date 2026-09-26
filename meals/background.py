"""Background work for the bot: chat-sized work off the event loop, and detached job processes.

ADR-0001 (docs/adr/ADR-0001-runtime-model.md), Decision and Ownership.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from typing import TypeVar

from telegram import Message, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_IN_FLIGHT = 2
DEADLINE_S = 15 * 60
ERROR_MAX_CHARS = 300
BUSY_TEXT = f"I'm already working on {MAX_IN_FLIGHT} requests. Try again in a few minutes."
TIMEOUT_TEXT = f"That took over {DEADLINE_S // 60} minutes, so I gave up. Try again later."
_ERROR_PREFIX = "Sorry, that didn't work: "

# Requests whose work hasn't finished. Only the event-loop thread touches it.
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
    global _in_flight
    message = update.effective_message
    if message is None:
        logger.warning("ignoring an update with no message to reply to")
        return
    if _in_flight >= MAX_IN_FLIGHT:
        logger.info("refused a request: %d already in flight", _in_flight)
        await message.reply_text(BUSY_TEXT, parse_mode=None)
        return
    _in_flight += 1  # no await since the check, so two handlers can't both take the last slot
    context.application.create_task(
        _run(message, work, render, ack), update=update, name="meals.background"
    )


async def _run(
    message: Message, work: Callable[[], T], render: Callable[[T], str], ack: str
) -> None:
    submitted = False
    try:
        await message.reply_text(ack, parse_mode=None)
        started = time.monotonic()
        future = asyncio.get_running_loop().run_in_executor(None, work)
        future.add_done_callback(_release)  # when the thread ends, not when we stop waiting
        submitted = True
        done, _ = await asyncio.wait({future}, timeout=DEADLINE_S)
        if not done:
            logger.warning("gave up after %ss; the abandoned work still holds its slot", DEADLINE_S)
            future.add_done_callback(lambda f: _log_abandoned(f, time.monotonic() - started))
            await message.reply_text(TIMEOUT_TEXT, parse_mode=None)
            return
        await message.reply_text(render(future.result()), parse_mode=None)
    except Exception as exc:
        logger.exception("background work failed")
        try:
            await message.reply_text(_one_line(exc), parse_mode=None)
        except Exception:
            logger.exception("couldn't send the error reply")
    finally:
        if not submitted:
            _release()


def _release(_future: object = None) -> None:
    global _in_flight
    _in_flight -= 1


def _log_abandoned(future: asyncio.Future[T], elapsed_s: float) -> None:
    error = None if future.cancelled() else future.exception()
    logger.info("abandoned work ended after %.0fs; its slot is free", elapsed_s, exc_info=error)


def _one_line(exc: Exception) -> str:
    """The first line of `exc`, or its type name if it has no text, cut to ERROR_MAX_CHARS."""
    text = str(exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    return (_ERROR_PREFIX + first)[:ERROR_MAX_CHARS]


def spawn_job(name: str, *args: str) -> int:
    """Start `python -m meals job <name> <args>` detached, in its own session; return its pid.

    The child outlives the bot (a restart or killpg of the bot's group doesn't reach it), reads
    no stdin, inherits stdout/stderr so a crash before the job sets up logging still lands in the
    spawner's log, and inherits none of the spawner's fds. It is never waited for. Raises OSError
    if it can't start: the caller tells Steve, because the message depends on what was starting.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "meals", "job", name, *args],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info("started job %s %s (pid %d)", name, " ".join(args), process.pid)
    return process.pid
