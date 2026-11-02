"""meals.bot's gate (the chat lock) and how build_app registers it.

Authority: PLAN.md, Phase 4 (lock the bot to Steve's chat ID); ADR-0001, Ownership (bot: the
chat-ID allowlist sits in one pre-dispatch `TypeHandler` in group -1, raising
ApplicationHandlerStop, so every handler inherits it); and the bot PR's Acceptance → proof table:
AC-1 (an update from another chat, or with no chat: no later handler runs, nothing is written,
nothing is replied) and its registration row (one `TypeHandler(Update, gate, block=True)` in group
-1, allowlist first, ApplicationHandlerStop; `AIORateLimiter(max_retries=3)`;
`concurrent_updates=False`; build_app registers the gate). PR 2a is the allowlist only, with no
update_id claim (that is bot PR 2b).

The pre-PR review's F4 (a FIX NOW) narrows AC-1 to
the update kinds the bot reads: the gate passes only `update.message` / `update.edited_message`
from the allowed chat with no `business_connection_id`; every other kind stops. PTB's
`effective_chat` also reads business messages (`_update.py:698-705`), whose chat can share an id
with a bot chat (`_message.py:637`), and callback queries, so each F4 case carries Steve's chat id.

Each dispatch test builds the real app (conftest) and adds a probe handler in group 1, after
/pantry's group 0: the probe runs only if nothing stopped the update before it.
`test_allowed_chat_reaches_later_groups` is the control that shows each "nothing happened"
observation in the other two would have seen something; the edited-message test is the same
control for the F4 cases.

The security review's S1 (2026-11-02) adds to AC-1: if the gate itself raises, the update stops
there too (fails closed) and the error is logged.
`test_allowed_chat_reaches_later_groups` is that test's control as well: the same /pantry, with a
gate that doesn't raise.

The post-PR review's C1 and C2 (2026-11-02) add to AC-1: C1, the update stops even when logging
itself raises (a log file that can't be opened makes a logger call raise, so the gate's drop log
raises, then the error handler's own log raises too, and PTB carries on to group 0); C2, the error
log never shows the bot's token, raw or with ':' percent-encoded as '%3A', when the error's text
echoes a Telegram URL (PTB copies an error body that isn't JSON into the error's text,
`telegram/request/_baserequest.py:334`).

The post-PR review's R3-1 and R3-2 (2026-11-02) add to AC-1: R3-1, a Telegram error's text is
untrusted and can encode the token any way, so a NetworkError whose text has the secret's '-' as
'%2D' or '&#45;' must not show the token either, and the log is searched once percent-decoded and
HTML-unescaped too; R3-2, the update stops when the gate raises and the error handler's own log
raises as well.
"""

import html
import logging
from urllib.parse import unquote

import pytest
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import AIORateLimiter, ContextTypes, TypeHandler

from meals.contracts import PantryItem
from meals.fakes import FakePantry
from test_bot.conftest import (
    ALLOWED_CHAT_ID,
    BOT_USER,
    MESSAGE_DATE,
    TOKEN,
    AnyApp,
    PantryOpener,
    RecordingRequest,
    build_bot,
    dispatch,
    text_update,
)

STRANGER_ID = 9191
GROUP_CHAT_ID = -100777
GATE_ERROR = "gate failure marker 5d2"  # the text of the error the broken gate raises
SECRET = TOKEN.partition(":")[2]  # a bot token's secret is the part after the colon
ENCODED_TOKEN = TOKEN.replace(":", "%3A")  # the bot's token as a percent-encoded URL carries it
SECRET_ENCODED_TOKEN = ENCODED_TOKEN.replace("-", "%2D")  # R3-1: encoded inside the secret too
SECRET_ESCAPED_TOKEN = TOKEN.replace("-", "&#45;")  # R3-1: HTML-escaped inside the secret
GATEWAY_PAGE = "<html><title>502 Bad Gateway</title><p>No answer from {url}</p></html>"


class Probe:
    """A handler for a later group: records the update_id of each update that reaches it."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def __call__(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.seen.append(update.update_id)


class RaisingLogHandler(logging.Handler):
    """A log destination that can't be written: each record raises OSError, as Python 3.11's
    `FileHandler(..., delay=True)` does when its file can't be opened (its `emit` opens the file
    outside any try, and `Handler.handle` has only try/finally)."""

    def emit(self, record: logging.LogRecord) -> None:
        raise OSError("the log file can't be opened")


def _shows_secret(text: str) -> bool:
    """Whether `text` shows the token's secret half, as written or once percent-decoded and
    HTML-unescaped (R3-1: Telegram's error text is untrusted and can encode the token any way)."""
    return SECRET in text or SECRET in unquote(html.unescape(text))


def _probed_bot(items: tuple[PantryItem, ...]) -> tuple[AnyApp, RecordingRequest, Probe]:
    """The real app over `items`, with a Probe in group 1."""
    app, request = build_bot(PantryOpener(FakePantry(items)))
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)
    return app, request, probe


@pytest.mark.parametrize("kind", ["message", "edited_message"])
@pytest.mark.parametrize(
    ("chat_id", "from_id"),
    [(STRANGER_ID, STRANGER_ID), (GROUP_CHAT_ID, ALLOWED_CHAT_ID)],
    ids=["stranger_in_their_own_chat", "steve_in_another_chat"],
)
def test_foreign_chat_stops_every_handler(
    chat_id: int, from_id: int, kind: str, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-1. The lock is on the chat, not the sender: Steve writing from another chat is still
    # another chat, so a gate keyed on the sender's id fails the second case. F4 passes edits too,
    # so an edit from another chat is checked the same way.
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    update = text_update(app.bot, 1, "/pantry", chat_id=chat_id, from_id=from_id, kind=kind)
    dispatch(app, update)

    assert probe.seen == [], "a later handler group ran for a foreign chat"
    assert opener.opened == 0, "the pantry was opened for a foreign chat"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"


def test_update_without_chat_is_dropped(sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # AC-1 "or with no chat": a missing chat is rejected. A button press on an inline message has a
    # sender, here Steve himself, but no chat.
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)
    sender = {"id": ALLOWED_CHAT_ID, "is_bot": False, "first_name": "S"}
    button = {"id": "cb1", "from": sender, "chat_instance": "ci1", "inline_message_id": "im1"}
    chatless = Update.de_json({"update_id": 2, "callback_query": button}, app.bot)
    assert chatless.effective_chat is None and chatless.effective_user is not None

    dispatch(app, chatless)

    assert probe.seen == [], "an update with no chat reached a later handler group"
    assert request.sent() == [], "the bot replied to an update with no chat"


def test_allowed_chat_reaches_later_groups(sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # Control for the two tests above: the same /pantry from Steve's chat opens the pantry and
    # replies (group 0), then reaches the probe (group 1). The gate returns, it doesn't stop.
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 1, "/pantry"))

    assert probe.seen == [1], "the allowed chat's update didn't reach a later handler group"
    assert opener.opened == 1
    assert request.sent(), "the allowed chat's /pantry got no reply"


def test_gate_failure_stops_every_handler(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # AC-1, the security review's S1: the gate fails closed. PTB's process_update catches a
    # handler's exception and, with no error handler registered, logs it and moves on to the next
    # group (telegram/ext/_application.py:1324-1327, process_error ~:1888), so a gate that raises
    # lets the update through to /pantry. The gate raises here for Steve's own /pantry, the
    # strongest case: when the lock can't decide, even the allowed chat stops. How the bot stops it
    # (an error handler, a wrapper in build_app) is open; the gate is replaced where build_app
    # reads it.
    ran: list[int] = []

    async def broken_gate(update: Update, *args: object, **kwargs: object) -> None:
        ran.append(update.update_id)
        raise RuntimeError(GATE_ERROR)

    monkeypatch.setattr("meals.bot.handlers.gate", broken_gate)
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 1, "/pantry"))

    assert ran == [1], "the replaced gate never ran: build_app no longer reads handlers.gate"
    assert opener.opened == 0, "the pantry was opened after the gate raised"
    assert probe.seen == [], "a later handler group ran after the gate raised"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"
    logged = [  # caplog formats each record as it arrives, filling exc_text
        record.getMessage() + (record.exc_text or "")
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.split(".")[0] == "meals"
    ]
    assert any(GATE_ERROR in text for text in logged), (
        "no meals ERROR record shows the gate's error"
    )


def test_logging_failure_still_stops_a_foreign_chat(
    caplog: pytest.LogCaptureFixture, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-1, the post-PR review's C1: the `meals` log can't be written, so a logger call raises a
    # plain OSError, not ApplicationHandlerStop. A stranger's /pantry makes the gate log its drop,
    # which raises; the error handler's own log then raises too, and PTB logs that and carries on to
    # the next group (telegram/ext/_application.py:1324-1327, 1929-1936). The lock must not depend
    # on logging working. The level lets every meals record reach the broken handler.
    caplog.set_level(logging.DEBUG, logger="meals")
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)
    meals_log = logging.getLogger("meals")
    broken = RaisingLogHandler()
    meals_log.addHandler(broken)
    try:
        dispatch(app, text_update(app.bot, 1, "/pantry", chat_id=STRANGER_ID))
    finally:
        meals_log.removeHandler(broken)

    assert opener.opened == 0, "the pantry was opened for a foreign chat when logging raised"
    assert probe.seen == [], "a later handler group ran for a foreign chat when logging raised"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"


def test_gate_and_logging_failure_still_stops_a_foreign_chat(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # AC-1, the post-PR review's R3-2: the C1 test above stops at the gate, so the error handler
    # never runs there. Here the gate raises (as in the S1 test), so the error handler runs, and
    # its own log raises too. An error handler that raises anything but ApplicationHandlerStop
    # makes PTB log that and carry on to the next group (telegram/ext/_application.py:1324-1327,
    # 1929-1936), so the lock must not depend on the error handler's log working. The gate is
    # replaced where build_app reads it; the level lets every meals record reach the broken handler.
    caplog.set_level(logging.DEBUG, logger="meals")
    ran: list[int] = []

    async def broken_gate(update: Update, *args: object, **kwargs: object) -> None:
        ran.append(update.update_id)
        raise RuntimeError(GATE_ERROR)

    monkeypatch.setattr("meals.bot.handlers.gate", broken_gate)
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)
    meals_log = logging.getLogger("meals")
    broken = RaisingLogHandler()
    meals_log.addHandler(broken)
    try:
        dispatch(app, text_update(app.bot, 1, "/pantry", chat_id=STRANGER_ID))
    finally:
        meals_log.removeHandler(broken)

    assert ran == [1], "the replaced gate never ran: build_app no longer reads handlers.gate"
    assert opener.opened == 0, "the pantry was opened when the gate and the error log both raised"
    assert probe.seen == [], "a later handler group ran when the gate and the error log both raised"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"


@pytest.mark.parametrize(
    ("echoed", "carrier"),
    [
        (TOKEN, RuntimeError),
        (ENCODED_TOKEN, RuntimeError),
        (SECRET_ENCODED_TOKEN, NetworkError),
        (SECRET_ESCAPED_TOKEN, NetworkError),
    ],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
def test_gate_error_log_never_shows_the_token(
    echoed: str,
    carrier: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # AC-1's error log, the post-PR review's C2: the gate raises an error whose text echoes a
    # Telegram URL carrying the bot's own token, the way PTB words a gateway's 502 page
    # (_baserequest.py:334). The update still stops and the error is still logged, but no meals
    # record shows the token: not raw, not with ':' as '%3A' (a redactor that replaces only the
    # exact token misses that one), and not the secret half alone. R3-1: the same page in a
    # NetworkError, the error PTB raises for it, with the secret's '-' as '%2D' or '&#45;' (a
    # redactor that replaces only the secret as written misses those), so each record is also
    # searched once decoded. The gate is replaced where build_app reads it, as in the S1 test above.
    caplog.set_level(logging.DEBUG, logger="meals")
    ran: list[int] = []
    page = GATEWAY_PAGE.format(url=f"https://api.telegram.org/bot{echoed}/sendMessage").encode()

    async def leaky_gate(update: Update, *args: object, **kwargs: object) -> None:
        ran.append(update.update_id)
        raise carrier(f"Bad Gateway (502). Parsing the server response {page!r} failed")

    monkeypatch.setattr("meals.bot.handlers.gate", leaky_gate)
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 1, "/pantry"))

    assert ran == [1], "the replaced gate never ran: build_app no longer reads handlers.gate"
    assert opener.opened == 0, "the pantry was opened after the gate raised"
    assert probe.seen == [], "a later handler group ran after the gate raised"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"
    records = [record for record in caplog.records if record.name.split(".")[0] == "meals"]
    assert any(record.levelno >= logging.ERROR for record in records), "no meals ERROR record"
    logged = [record.getMessage() + (record.exc_text or "") for record in records]
    assert [text for text in logged if _shows_secret(text)] == [], "a meals record has the token"


def test_business_message_with_allowed_chat_id_is_dropped(
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # F4: a business chat that shares Steve's chat id is not Steve's chat with the bot.
    app, request, probe = _probed_bot(sample_pantry_items)
    update = text_update(
        app.bot, 1, "/pantry", kind="business_message", business_connection_id="bc1"
    )
    assert update.effective_chat is not None and update.effective_chat.id == ALLOWED_CHAT_ID

    dispatch(app, update)

    assert probe.seen == [], "a business message reached a later handler group"
    assert request.sent() == [], "the bot replied to a business message"


def test_message_with_business_connection_is_dropped(
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # F4: a message, from Steve's chat id, that belongs to a business connection.
    app, request, probe = _probed_bot(sample_pantry_items)

    dispatch(app, text_update(app.bot, 1, "/pantry", business_connection_id="bc1"))

    assert probe.seen == [], "a message with a business_connection_id reached a later group"
    assert request.sent() == [], "the bot replied to a message with a business_connection_id"


def test_non_message_update_is_dropped(sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # F4: Steve presses a button on a message in his own chat. The update has his chat (through
    # the button's message) and a message (`effective_message`), but it is a callback_query.
    app, request, probe = _probed_bot(sample_pantry_items)
    sender = {"id": ALLOWED_CHAT_ID, "is_bot": False, "first_name": "S"}
    shown = {
        "message_id": 5,
        "date": MESSAGE_DATE,
        "chat": {"id": ALLOWED_CHAT_ID, "type": "private"},
        "from": BOT_USER,
        "text": "pick one",
    }
    button = {"id": "cb2", "from": sender, "chat_instance": "ci2", "message": shown, "data": "1"}
    pressed = Update.de_json({"update_id": 3, "callback_query": button}, app.bot)
    assert pressed.effective_chat is not None and pressed.effective_chat.id == ALLOWED_CHAT_ID
    assert pressed.effective_message is not None

    dispatch(app, pressed)

    assert probe.seen == [], "a callback_query reached a later handler group"
    assert request.sent() == [], "the bot replied to a callback_query"


def test_edited_message_from_allowed_chat_passes_the_gate(
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # Control for the F4 drops above: F4 keeps edited_message. Plain text, so no group-0 command
    # handles it (what an edit does is 2b's D7).
    app, _, probe = _probed_bot(sample_pantry_items)

    dispatch(app, text_update(app.bot, 4, "out of eggs", kind="edited_message"))

    assert probe.seen == [4], "an edited message from the allowed chat was stopped"


def test_app_is_sequential_and_rate_limited(fake_pantry: FakePantry) -> None:
    # AC-1's registration row in the bot PR's Acceptance → proof table: flood-control retries via
    # AIORateLimiter(max_retries=3), and updates processed one at a time (concurrent_updates=False),
    # so the gate sees them in arrival order.
    app, _ = build_bot(PantryOpener(fake_pantry))

    limiter = app.bot.rate_limiter
    assert isinstance(limiter, AIORateLimiter), f"rate limiter is {limiter!r}"
    assert limiter._max_retries == 3  # PTB keeps max_retries only in this attribute
    assert app.concurrent_updates == 1


def test_gate_is_blocking_in_group_minus_one(fake_pantry: FakePantry) -> None:
    # ADR-0001, Ownership, and AC-1's registration row: one TypeHandler(Update, gate, block=True) in
    # group -1, and the allowlist runs first, so no group comes before it. block=True explicitly:
    # PTB's default is DEFAULT_TRUE, which a Defaults(block=False) would turn into a non-blocking
    # gate whose stop can't stop anything.
    app, _ = build_bot(PantryOpener(fake_pantry))

    assert min(app.handlers) == -1, f"handler groups: {sorted(app.handlers)}"
    assert len(app.handlers[-1]) == 1, f"group -1 holds {app.handlers[-1]}"
    gate = app.handlers[-1][0]
    assert isinstance(gate, TypeHandler) and gate.type is Update, f"the gate is {gate!r}"
    assert gate.block is True
