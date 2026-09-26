"""Background work for the bot: chat-sized work off the event loop, and detached job processes.
Also the Telegram plumbing the bot and the jobs share: splitting, one-line errors, and a sender
for a job process, which has no bot Application of its own.

ADR-0001 (docs/adr/ADR-0001-runtime-model.md), Decision and Ownership.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TypeVar

import telegram
from telegram import Message, Update
from telegram.constants import MessageLimit
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_IN_FLIGHT = 2
DEADLINE_S = 15 * 60
ERROR_MAX_CHARS = 300
BUSY_TEXT = f"I'm already working on {MAX_IN_FLIGHT} requests. Try again in a few minutes."
TIMEOUT_TEXT = f"That took over {DEADLINE_S // 60} minutes, so I gave up. Try again later."
_ERROR_PREFIX = "Sorry, that didn't work: "
SEND_ATTEMPTS = 3
SEND_BUDGET_S = 120
_BACKOFF_S = (5.0, 15.0)

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
        try:
            await _reply(message, BUSY_TEXT)
        except Exception:
            logger.exception("couldn't send the busy reply")
        return
    _in_flight += 1  # no await since the check, so two handlers can't both take the last slot
    context.application.create_task(
        _run(message, work, render, ack), update=update, name="meals.background"
    )


async def _run(
    message: Message, work: Callable[[], T], render: Callable[[T], str], ack: str
) -> None:
    future: asyncio.Future[T] | None = None
    try:
        await _reply(message, ack)
        started = time.monotonic()
        future = asyncio.get_running_loop().run_in_executor(None, work)
        future.add_done_callback(_release)  # when the thread ends, not when we stop waiting
        done, _ = await asyncio.wait({future}, timeout=DEADLINE_S)
        if not done:
            logger.warning("gave up after %ss; the abandoned work still holds its slot", DEADLINE_S)
            future.add_done_callback(lambda f: _log_abandoned(f, time.monotonic() - started))
            await _reply(message, TIMEOUT_TEXT)
            return
        await _reply(message, render(future.result()))
    except Exception as exc:
        logger.exception("background work failed")
        try:
            await _reply(message, _one_line(exc))
        except Exception:
            logger.exception("couldn't send the error reply")
    finally:
        if future is None:  # work never started, so no done-callback will free the slot
            _release()


async def _reply(message: Message, text: str) -> None:
    """Send `text` as plain text (replies carry web and Claude text), split at Telegram's limit.

    Empty text is still sent, so Telegram rejects it and the caller's error path reports it.
    """
    for chunk in split_message(text):
        await message.reply_text(chunk, parse_mode=None)


def split_message(text: str) -> list[str]:
    """`text` in chunks within Telegram's message limit. Empty text stays one (empty) chunk."""
    size = MessageLimit.MAX_TEXT_LENGTH
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


def _release(_future: object = None) -> None:
    global _in_flight
    _in_flight -= 1


def _log_abandoned(future: asyncio.Future[T], elapsed_s: float) -> None:
    error = None if future.cancelled() else future.exception()
    logger.warning("abandoned work ended after %.0fs; its slot is free", elapsed_s, exc_info=error)


def _one_line(exc: Exception) -> str:
    """The error reply: `exc`'s first line after the prefix, cut to ERROR_MAX_CHARS."""
    return (_ERROR_PREFIX + first_line(exc))[:ERROR_MAX_CHARS]


def first_line(exc: BaseException) -> str:
    """The first line of `exc`'s text, or its type name if it has none. Chat replies and job
    reports show only this: the rest (a traceback, a scraped page) belongs in the log."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


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


# ── sending from a job process ───────────────────────────────────────────────


class DeliveryFailed(Exception):
    """A Telegram message wasn't delivered: retries ran out, or Telegram refused it."""


class TelegramSend:
    """`jobs.Deps.send` for a job process: plain text to Steve's chat, split at Telegram's limit, with a
    fresh `telegram.Bot` per attempt. Transient errors (network, flood control) are retried up to
    SEND_ATTEMPTS within SEND_BUDGET_S, which counts the attempts themselves as well as the waits
    (no attempt or wait starts past it); anything else, or running out, raises DeliveryFailed.

    DeliveryFailed carries only the error's type, never its text, and drops the chain: PTB's
    InvalidToken message includes the token."""

    def __init__(
        self,
        token: str,
        chat_id: int,
        *,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._sleep = sleep
        self._clock = clock

    def __call__(self, text: str) -> None:
        pending = list(split_message(text))
        spent = 0.0  # attempts timed on the clock, plus each wait (the sleep may be injected)
        for attempt in range(1, SEND_ATTEMPTS + 1):
            began = self._clock()
            try:
                asyncio.run(self._send(pending))
                return
            except TelegramError as exc:
                spent += self._clock() - began
                wait = _retry_wait(exc, attempt)
                kind = type(exc).__name__
                if wait is None or attempt == SEND_ATTEMPTS or spent + wait > SEND_BUDGET_S:
                    raise DeliveryFailed(f"Telegram didn't take the message ({kind})") from None
                logger.warning("Telegram send attempt %d failed (%s); retrying", attempt, kind)
                self._sleep(wait)
                spent += wait

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
