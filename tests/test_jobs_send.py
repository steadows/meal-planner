"""meals.jobs.TelegramSend, meals.background.split_message, and the jobs module's constants.

Authority: the P2 seam map (.context/seams/P2.md: the `TelegramSend` row; the reuse table's EXTEND row,
"lift it into a public background.split_message(text) -> list[str] (empty text → [text]), used by
_reply and the jobs sender. This is one home for one concept"; the `JOBS` and constants rows) and
ADR-0001 :146 (sends retry transient Telegram errors: 3 attempts, ≤ 2 min).

PTB's `telegram.Bot` is replaced by a local fake that records each Bot's token and each
send_message call, and raises whatever a test scripts. House style from test_background.py: patch
the attribute and any alias of it in meals.jobs. `sleep` is injected, so nothing waits.
"""

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest
import telegram
from telegram import Message
from telegram.constants import MessageLimit
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from meals import background

_MISSING: ImportError | None = None
try:
    from meals import jobs
except ImportError as exc:  # RED: meals/jobs.py isn't written yet
    _MISSING = exc

LIMIT = MessageLimit.MAX_TEXT_LENGTH
TOKEN = "123456:fake-token"
CHAT_ID = 4242
BUDGET_S = 120  # ADR :146, "≤ 2 min"
TEXT = "Sheet-pan chicken fajitas, peppers on the side.\n" * 200  # 9,600 characters


@pytest.fixture
def _jobs_exists() -> None:
    if _MISSING is not None:
        pytest.fail(
            f"meals.jobs is not built yet (P2 seam map, `meals/jobs.py`): {_MISSING}", pytrace=False
        )


class FakeTelegramApi:
    """What the fake Bot saw. `failures` scripts one outcome per send (an exception, or None to
    deliver); once it's used up, `always` (default None) applies."""

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.sends: list[tuple[str, str, object]] = []  # (chat id, text, parse_mode)
        self.failures: list[Exception | None] = []
        self.always: Exception | None = None
        self.clock = 0.0  # fake monotonic seconds: each send costs `send_cost_s`
        self.send_cost_s = 0.0
        self.send_started: list[float] = []

    def record(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        text = kwargs.get("text", args[1] if len(args) > 1 else None)
        self.sends.append((str(chat_id), str(text), kwargs.get("parse_mode")))
        self.send_started.append(self.clock)
        self.clock += self.send_cost_s
        failure = self.failures.pop(0) if self.failures else self.always
        if failure is not None:
            raise failure

    @property
    def texts(self) -> list[str]:
        return [text for _, text, _ in self.sends]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, _jobs_exists: None) -> FakeTelegramApi:
    api = FakeTelegramApi()

    class FakeBot:
        def __init__(self, token: str, *args: Any, **kwargs: Any) -> None:
            api.tokens.append(token)

        async def __aenter__(self) -> "FakeBot":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(0)
            api.record(args, kwargs)

    real = telegram.Bot
    monkeypatch.setattr(telegram, "Bot", FakeBot)
    for name, value in list(vars(jobs).items()):  # `from telegram import Bot`
        if value is real:
            monkeypatch.setattr(jobs, name, FakeBot)
    return api


def _sender(sleeps: list[float]) -> Callable[[str], None]:
    return jobs.TelegramSend(TOKEN, CHAT_ID, sleep=sleeps.append)


# ── constants ────────────────────────────────────────────────────────────────


def test_the_jobs_and_limits_are_the_seam_maps(_jobs_exists: None) -> None:
    expected = {"sat_propose", "sat_nudge", "sun_autoapprove", "cart_fill", "reconcile"}
    assert set(jobs.JOBS) == expected and len(jobs.JOBS) == 5
    assert (jobs.ALARM_S, jobs.SEND_ATTEMPTS, jobs.SEND_BUDGET_S) == (45 * 60, 3, BUDGET_S)
    assert str(jobs.TZ) == "America/Detroit"


# ── TelegramSend ─────────────────────────────────────────────────────────────


def test_sends_plain_text_to_the_configured_chat(api: FakeTelegramApi) -> None:
    _sender([])("Week of Sep 27: pick 2-3")

    assert api.tokens and set(api.tokens) == {TOKEN}
    assert api.sends == [(str(CHAT_ID), "Week of Sep 27: pick 2-3", None)]


def test_a_long_message_goes_out_in_plain_text_chunks_within_telegrams_limit(
    api: FakeTelegramApi,
) -> None:
    text = TEXT[: 2 * LIMIT + 10]

    _sender([])(text)

    assert len(api.texts) >= 3 and all(0 < len(chunk) <= LIMIT for chunk in api.texts)
    assert "".join(api.texts) == text
    assert all(parse_mode is None for *_, parse_mode in api.sends)


def test_each_message_gets_a_fresh_bot(api: FakeTelegramApi) -> None:
    # Seam map: asyncio.run per message with a fresh telegram.Bot.
    send = _sender([])

    send("first")
    built = len(api.tokens)
    send("second")

    assert built >= 1 and len(api.tokens) > built


@pytest.mark.parametrize(
    "failures",
    [
        pytest.param([NetworkError("connection reset"), TimedOut()], id="network-then-timeout"),
        pytest.param([RetryAfter(5)], id="retry-after"),
    ],
)
def test_transient_errors_are_retried_within_three_attempts(
    api: FakeTelegramApi, failures: list[Exception | None]
) -> None:
    api.failures = [*failures, None]
    sleeps: list[float] = []

    _sender(sleeps)("Cart ready")

    assert api.texts == ["Cart ready"] * (len(failures) + 1)
    assert sum(sleeps) <= BUDGET_S


def test_retry_after_waits_as_long_as_telegram_asks(api: FakeTelegramApi) -> None:
    api.failures = [RetryAfter(5), None]
    sleeps: list[float] = []

    _sender(sleeps)("Cart ready")

    assert any(seconds >= 5 for seconds in sleeps), sleeps


def test_three_transient_failures_raise_delivery_failed(api: FakeTelegramApi) -> None:
    api.always = NetworkError("connection reset")
    sleeps: list[float] = []

    with pytest.raises(jobs.DeliveryFailed):
        _sender(sleeps)("Cart ready")

    assert len(api.sends) == 3
    assert sum(sleeps) <= BUDGET_S


@pytest.mark.parametrize(
    "error",
    [BadRequest("Chat not found"), Forbidden("Forbidden: bot was blocked by the user")],
    ids=["bad-request", "forbidden"],
)
def test_non_transient_errors_raise_delivery_failed_without_retrying(
    api: FakeTelegramApi, error: Exception
) -> None:
    # Seam map: BadRequest is a NetworkError subclass in PTB, and still isn't retried.
    api.always = error
    sleeps: list[float] = []

    with pytest.raises(jobs.DeliveryFailed):
        _sender(sleeps)("Cart ready")

    assert (len(api.sends), sleeps) == (1, [])


@pytest.mark.parametrize("retry_after", [100, 300])
def test_waiting_never_passes_the_two_minute_budget(api: FakeTelegramApi, retry_after: int) -> None:
    # Seam map: it waits `retry_after` only if that fits the budget; 3 attempts within 120 s total.
    api.always = RetryAfter(retry_after)
    sleeps: list[float] = []

    with pytest.raises(jobs.DeliveryFailed):
        _sender(sleeps)("Cart ready")

    assert sum(sleeps) <= BUDGET_S, sleeps
    assert len(api.sends) <= 3


# ── split_message ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("length", [0, 1, LIMIT, LIMIT + 1, 2 * LIMIT + 10])
def test_split_message_keeps_every_character_in_chunks_within_the_limit(length: int) -> None:
    text = TEXT[:length]

    chunks = background.split_message(text)

    assert "".join(chunks) == text
    if length <= LIMIT:
        assert chunks == [text]  # empty text included: it's still sent, and Telegram rejects it
    else:
        assert len(chunks) >= 2 and all(0 < len(chunk) <= LIMIT for chunk in chunks)


def _split_spy(text: str) -> list[str]:
    return ["chunk one", "chunk two"]


def test_bot_replies_split_through_split_message(monkeypatch: pytest.MonkeyPatch) -> None:
    # One home for splitting: `_reply` (P1) calls the public function rather than its own copy.
    monkeypatch.setattr(background, "split_message", _split_spy)
    replies: list[str] = []

    class Replies:
        async def reply_text(self, text: str, **kwargs: Any) -> None:
            replies.append(text)

    asyncio.run(background._reply(cast(Message, Replies()), "the whole reply"))

    assert replies == ["chunk one", "chunk two"]


def test_telegram_send_splits_through_split_message(
    api: FakeTelegramApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One home for splitting: the jobs sender uses background's, whether imported as a module
    # attribute or by name.
    original = background.split_message
    monkeypatch.setattr(background, "split_message", _split_spy)
    for name, value in list(vars(jobs).items()):
        if value is original:
            monkeypatch.setattr(jobs, name, _split_spy)

    _sender([])("the whole message")

    assert api.texts == ["chunk one", "chunk two"]


# ── Codex-sweep repair (seam map D21) ────────────────────────────────────────


def test_the_two_minute_budget_covers_the_sends_themselves_not_just_the_waits(
    api: FakeTelegramApi,
) -> None:
    # D21 (C3): the 120 s budget covers the whole send on a monotonic clock (`clock=`), so no
    # attempt or wait starts past it. Each attempt here takes 100 s of the fake clock.
    api.always = NetworkError("connection reset")
    api.send_cost_s = 100.0
    waits: list[tuple[float, float]] = []  # (clock when the wait starts, seconds)

    def sleep(seconds: float) -> None:
        waits.append((api.clock, seconds))
        api.clock += seconds

    send = jobs.TelegramSend(TOKEN, CHAT_ID, sleep=sleep, clock=lambda: api.clock)
    with pytest.raises(jobs.DeliveryFailed):
        send("Cart ready")

    assert api.send_started and all(start <= BUDGET_S for start in api.send_started), (
        api.send_started
    )
    assert all(start <= BUDGET_S for start, _ in waits), waits
