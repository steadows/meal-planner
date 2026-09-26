"""meals.background: `start` (chat-sized work in a worker thread, capped and deadlined) and
`spawn_job` (a detached `python -m meals job` child).

Authority: the P1 seam map (.context/seams/P1.md: the `start` contract steps 1-6, slot lifetime,
the `spawn_job` contract, Test notes) and ADR-0001 (Decision :47-49, Consequences :255-258,
Integration Test Points :445-453). PTB's objects are local fakes passed through `cast`: a message
that records each reply, an update that carries it, and a context whose `application.create_task`
keeps the task so the test can await it. A blocking `work` waits on a `threading.Event` for at
most BLOCK_S, and every test releases its events before `asyncio.run` joins the worker threads.
"""

import asyncio
import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from meals import background
from meals.background import BUSY_TEXT, DEADLINE_S, ERROR_MAX_CHARS, MAX_IN_FLIGHT, TIMEOUT_TEXT
from meals.contracts import ClaudeRunnerError

BLOCK_S = 5.0  # the longest a blocked `work` waits, so a broken `start` can't hang the suite
WAIT_S = 3.0  # the longest a test polls for a condition
POLL_S = 0.01
SEND_S = 0.005  # a fake reply takes this long, like a real send; the loop is free meanwhile
RETURN_S = 1.0  # `start` must return well within this while its work is still blocked
SHORT_DEADLINE_S = 0.2

ACK = "Looking for recipes..."
RESULT = "three sheet-pan dinners"


class FakeMessage:
    """Records each reply as (text, kwargs). From reply number `fail_from` on (0-based), the
    reply is recorded and then raises, like a send Telegram rejected."""

    def __init__(self, fail_from: int | None = None) -> None:
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self._fail_from = fail_from

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        await asyncio.sleep(SEND_S)
        self.replies.append((text, kwargs))
        if self._fail_from is not None and len(self.replies) > self._fail_from:
            raise NetworkError("fake send failed")


class FakeUpdate:
    def __init__(self, message: FakeMessage | None) -> None:
        self.effective_message = message


class FakeApplication:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[Any]] = []

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        update: object | None = None,
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


class FakeContext:
    def __init__(self) -> None:
        self.application = FakeApplication()


class Blocker:
    """A `work` that blocks until `release` is set (at most BLOCK_S), then returns "unblocked"."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self) -> str:
        self.started.set()
        self.release.wait(BLOCK_S)
        return "unblocked"


class Render:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, result: object) -> str:
        self.calls.append(result)
        return f"rendered: {result}"


@pytest.fixture(autouse=True)
def _nothing_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background, "_in_flight", 0)


async def _start(
    message: FakeMessage | None,
    context: FakeContext,
    work: Callable[[], object],
    render: Callable[[object], str],
    ack: str = ACK,
) -> None:
    await background.start(
        cast(Update, FakeUpdate(message)),
        cast(ContextTypes.DEFAULT_TYPE, context),
        work,
        render,
        ack,
    )


async def _drain(context: FakeContext) -> None:
    """Wait for every task `start` handed to create_task. None may end with an exception."""
    tasks = list(context.application.tasks)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=WAIT_S)
        assert not pending, "a background task did not finish"
    for task in tasks:
        assert task.exception() is None, f"an exception escaped the task: {task.exception()!r}"


async def _wait_until(condition: Callable[[], object], what: str) -> None:
    for _ in range(int(WAIT_S / POLL_S)):
        if condition():
            return
        await asyncio.sleep(POLL_S)
    pytest.fail(f"timed out waiting until {what}")


def _texts(message: FakeMessage) -> list[str]:
    """The reply texts, after checking that every reply was sent with parse_mode=None."""
    for text, kwargs in message.replies:
        assert "parse_mode" in kwargs and kwargs["parse_mode"] is None, (
            f"reply {text!r} was not sent as plain text (explicit parse_mode=None): {kwargs}"
        )
    return [text for text, _ in message.replies]


def _in_flight() -> int:
    return background._in_flight


# ── start: ack, work, render ─────────────────────────────────────────────────


def test_ack_then_work_in_a_worker_thread_then_render() -> None:
    message = FakeMessage()
    context = FakeContext()
    render = Render()
    seen: dict[str, object] = {}

    def work() -> str:
        seen["thread"] = threading.get_ident()
        seen["replies"] = [text for text, _ in message.replies]
        return RESULT

    async def scenario() -> int:
        await _start(message, context, work, render)
        assert context.application.tasks, "start must hand the request to application.create_task"
        await _drain(context)
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())

    assert seen["replies"] == [ACK], "the ack must be sent before work runs"
    assert seen["thread"] != loop_thread, "work must run off the event-loop thread"
    assert render.calls == [RESULT]
    assert _texts(message) == [ACK, f"rendered: {RESULT}"]


def test_second_update_is_acknowledged_while_the_first_work_is_blocked() -> None:
    first, second = FakeMessage(), FakeMessage()
    context = FakeContext()
    blocker = Blocker()

    async def start_at_once(message: FakeMessage, work: Callable[[], object], ack: str) -> None:
        try:
            await asyncio.wait_for(_start(message, context, work, Render(), ack), RETURN_S)
        except TimeoutError:
            pytest.fail("start() did not return while a work was still blocked")

    async def scenario() -> None:
        try:
            await start_at_once(first, blocker, "first ack")
            await _wait_until(blocker.started.is_set, "the first work started")
            await start_at_once(second, lambda: RESULT, "second ack")
            await _wait_until(lambda: second.replies, "the second update was acknowledged")
            assert _texts(first) == ["first ack"]
        finally:
            blocker.release.set()
        await _drain(context)

    asyncio.run(scenario())

    assert _texts(first) == ["first ack", "rendered: unblocked"]
    assert _texts(second) == ["second ack", f"rendered: {RESULT}"]


# ── start: the in-flight cap ─────────────────────────────────────────────────


def test_cap_and_deadline_are_the_adrs() -> None:
    # ADR-0001 Decision :47-49 (at most 2 in flight), Consequences :255-256 (gives up after 15 min).
    assert MAX_IN_FLIGHT == 2
    assert DEADLINE_S == 15 * 60


def test_request_beyond_the_cap_gets_busy_and_its_work_never_runs() -> None:
    context = FakeContext()
    blockers = [Blocker() for _ in range(MAX_IN_FLIGHT)]
    running = [FakeMessage() for _ in blockers]
    refused, refused_work = FakeMessage(), Blocker()

    async def scenario() -> None:
        try:
            for message, blocker in zip(running, blockers, strict=True):
                await _start(message, context, blocker, Render())
            await _wait_until(lambda: all(b.started.is_set() for b in blockers), "the works ran")
            await _start(refused, context, refused_work, Render())
            await _wait_until(lambda: refused.replies, "the refused request got a reply")
        finally:
            for blocker in [*blockers, refused_work]:
                blocker.release.set()
        await _drain(context)

    asyncio.run(scenario())

    assert _texts(refused) == [BUSY_TEXT]
    assert not refused_work.started.is_set()
    for message in running:
        assert _texts(message) == [ACK, "rendered: unblocked"]


def test_simultaneous_starts_never_exceed_the_cap() -> None:
    # Seam map, `start` step 3: the slot is taken with no await between the check and the take.
    context = FakeContext()
    blockers = [Blocker() for _ in range(MAX_IN_FLIGHT + 1)]
    messages = [FakeMessage() for _ in blockers]

    async def scenario() -> None:
        try:
            await asyncio.gather(
                *(_start(m, context, b, Render()) for m, b in zip(messages, blockers, strict=True))
            )
            await _wait_until(lambda: all(m.replies for m in messages), "every request got a reply")
        finally:
            for blocker in blockers:
                blocker.release.set()
        await _drain(context)

    asyncio.run(scenario())

    replies = [_texts(message) for message in messages]
    assert replies.count([BUSY_TEXT]) == 1, replies
    assert replies.count([ACK, "rendered: unblocked"]) == MAX_IN_FLIGHT, replies
    assert sum(blocker.started.is_set() for blocker in blockers) == MAX_IN_FLIGHT


# ── start: failures ──────────────────────────────────────────────────────────

FIRST_LINE = "Mealie returned 502 for /api/recipes"
LATER_LINE = "upstream said: <html>Bad Gateway</html>"


class QuietFailure(Exception):
    pass


def _error_reply(work: Callable[[], object], render: Callable[[object], str]) -> str:
    """Run one request whose work or render fails; return its error reply, the one after the ack."""
    message = FakeMessage()
    context = FakeContext()

    async def scenario() -> None:
        await _start(message, context, work, render)
        await _drain(context)

    asyncio.run(scenario())
    texts = _texts(message)
    assert len(texts) == 2 and texts[0] == ACK, texts
    reply = texts[1]
    assert reply.startswith("Sorry, that didn't work: "), reply
    assert "\n" not in reply
    assert len(reply) <= ERROR_MAX_CHARS
    return reply


def _raise(error: Exception) -> Callable[[], object]:
    def work() -> object:
        raise error

    return work


@pytest.mark.parametrize("failing", ["work", "render"])
def test_failure_gets_one_single_line_reply(failing: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="meals.background")
    error = RuntimeError(f"{FIRST_LINE}\n{LATER_LINE}")
    render = Render()

    def failing_render(result: object) -> str:
        render(result)
        raise error

    if failing == "work":
        reply = _error_reply(_raise(error), render)
        assert render.calls == []
    else:
        reply = _error_reply(lambda: RESULT, failing_render)
        assert render.calls == [RESULT]

    assert FIRST_LINE in reply
    assert "Bad Gateway" not in reply
    assert any(
        record.exc_info and record.exc_info[1] is error
        for record in caplog.records
        if record.name == "meals.background"
    ), "the failure must be logged with its traceback"


def test_long_error_reply_is_cut_to_error_max_chars() -> None:
    reply = _error_reply(_raise(RuntimeError("Mealie said: " + "slow " * 200)), Render())

    assert "Mealie said: slow slow" in reply


@pytest.mark.parametrize("text", ["", "   "], ids=["empty", "spaces"])
def test_blank_error_reply_names_the_exception_type(text: str) -> None:
    reply = _error_reply(_raise(QuietFailure(text)), Render())

    assert "QuietFailure" in reply


def test_claude_error_reply_carries_the_reason_never_the_raw_output() -> None:
    error = ClaudeRunnerError("claude timed out after 600s", raw_output="<b>INJECTED</b>")

    reply = _error_reply(_raise(error), Render())

    assert "claude timed out after 600s" in reply
    assert "INJECTED" not in reply
    assert "<b>" not in reply


# ── start: the deadline and slot lifetime ────────────────────────────────────


@pytest.mark.parametrize("late", ["returns", "raises"])
def test_hung_work_gets_timeout_text_and_nothing_after_its_thread_ends(
    late: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(background, "DEADLINE_S", SHORT_DEADLINE_S)
    caplog.set_level(logging.DEBUG, logger="meals.background")
    context = FakeContext()
    hung, hung_render, message = Blocker(), Render(), FakeMessage()
    after, after_render = FakeMessage(), Render()

    def hung_work() -> str:
        result = hung()
        if late == "raises":
            raise RuntimeError("the abandoned work failed after the deadline")
        return result

    async def scenario() -> None:
        try:
            await _start(message, context, hung_work, hung_render)
            await _wait_until(lambda: len(message.replies) >= 2, "the deadline reply")
            assert [
                record
                for record in caplog.records
                if record.name == "meals.background" and record.levelno == logging.WARNING
            ], "the deadline must log a WARNING that the abandoned work still holds its slot"
        finally:
            hung.release.set()
        await _wait_until(lambda: _in_flight() == 0, "the hung thread ended and freed its slot")
        # A late reply scheduled when the thread ended finishes before this whole request does.
        await _start(after, context, lambda: RESULT, after_render)
        await _drain(context)

    asyncio.run(scenario())

    assert _texts(message) == [ACK, TIMEOUT_TEXT]
    assert hung_render.calls == []
    assert _texts(after) == [ACK, f"rendered: {RESULT}"]


def test_slot_stays_held_after_the_deadline_until_the_thread_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background, "DEADLINE_S", SHORT_DEADLINE_S)
    context = FakeContext()
    hung = [Blocker() for _ in range(MAX_IN_FLIGHT)]
    hung_messages = [FakeMessage() for _ in hung]
    refused, refused_work = FakeMessage(), Blocker()
    after = FakeMessage()

    async def scenario() -> None:
        try:
            for message, blocker in zip(hung_messages, hung, strict=True):
                await _start(message, context, blocker, Render())
            await _wait_until(
                lambda: all(len(m.replies) >= 2 for m in hung_messages), "every deadline reply"
            )
            await _start(refused, context, refused_work, Render())
            await _wait_until(lambda: refused.replies, "the refused request got a reply")
        finally:
            for blocker in [*hung, refused_work]:
                blocker.release.set()
        await _wait_until(lambda: _in_flight() == 0, "the hung threads ended and freed their slots")
        await _start(after, context, lambda: RESULT, Render())
        await _drain(context)

    asyncio.run(scenario())

    for message in hung_messages:
        assert _texts(message) == [ACK, TIMEOUT_TEXT]
    assert _texts(refused) == [BUSY_TEXT]
    assert not refused_work.started.is_set()
    assert _texts(after) == [ACK, f"rendered: {RESULT}"]


@pytest.mark.parametrize(
    "outcome",
    ["success", "work_raises", "ack_send_fails", "result_send_fails"],
)
def test_slot_is_released_and_nothing_escapes(outcome: str) -> None:
    # Seam map, `start` step 5 and slot lifetime. A failing send fails the error reply too.
    fail_from = {"ack_send_fails": 0, "result_send_fails": 1}.get(outcome)
    message = FakeMessage(fail_from=fail_from)
    context = FakeContext()
    after = FakeMessage()
    ran: list[bool] = []

    def work() -> str:
        ran.append(True)
        if outcome == "work_raises":
            raise RuntimeError("Mealie is down")
        return RESULT

    async def scenario() -> None:
        await _start(message, context, work, Render())
        await _drain(context)
        await _wait_until(lambda: _in_flight() == 0, f"the slot was released after {outcome}")
        await _start(after, context, lambda: RESULT, Render())
        await _drain(context)

    asyncio.run(scenario())

    assert _texts(message)[:1] == [ACK]
    if outcome == "ack_send_fails":
        # Seam map: a failed ack takes the error path (the best-effort error line), work never runs.
        assert ran == []
        assert len(message.replies) == 2
    else:
        assert ran == [True]
    assert _texts(after) == [ACK, f"rendered: {RESULT}"]


def test_update_without_a_message_is_ignored() -> None:
    context = FakeContext()
    ignored_work = Blocker()
    message = FakeMessage()
    ran: list[bool] = []

    def work() -> str:
        ran.append(True)
        return RESULT

    async def scenario() -> None:
        try:
            for _ in range(MAX_IN_FLIGHT + 1):
                await _start(None, context, ignored_work, Render())
            await _start(message, context, work, Render())
        finally:
            ignored_work.release.set()
        await _drain(context)
        await _wait_until(lambda: _in_flight() == 0, "every slot was released")

    asyncio.run(scenario())

    assert not ignored_work.started.is_set()
    assert ran == [True]
    assert _texts(message) == [ACK, f"rendered: {RESULT}"]


# ── spawn_job ────────────────────────────────────────────────────────────────


SPAWNED_PID = 48213


@pytest.mark.parametrize(
    ("name", "args"),
    [("cart_fill", ("--week", "2026-09-28")), ("reconcile", ())],
)
def test_spawn_job_starts_a_detached_meals_job_child(
    monkeypatch: pytest.MonkeyPatch, name: str, args: tuple[str, ...]
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def popen_spy(*positional: object, **kwargs: object) -> SimpleNamespace:
        calls.append((positional, kwargs))
        return SimpleNamespace(pid=SPAWNED_PID)  # no wait(): waiting fails as AttributeError

    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", popen_spy)
    for attribute, value in list(vars(background).items()):  # `from subprocess import Popen`
        if value is real_popen:
            monkeypatch.setattr(background, attribute, popen_spy)

    pid = background.spawn_job(name, *args)

    assert len(calls) == 1
    positional, kwargs = calls[0]
    assert len(positional) <= 1, "pass Popen's options by keyword"
    argv = cast(Sequence[str], positional[0] if positional else kwargs["args"])
    assert list(argv) == [sys.executable, "-m", "meals", "job", name, *args]
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("stdin") == subprocess.DEVNULL
    assert not kwargs.get("shell")
    assert kwargs.get("close_fds", True) is not False
    assert not kwargs.get("pass_fds"), "the job child must not inherit the bot's fds"
    assert kwargs.get("stdout") is None and kwargs.get("stderr") is None, "inherit the log"
    assert pid == SPAWNED_PID


def test_spawn_job_lets_oserror_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python"))

    with pytest.raises(OSError):
        background.spawn_job("no_such_job_for_tests")
