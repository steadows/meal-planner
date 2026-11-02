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

The post-PR review's R4-1 (2026-11-02) changes AC-1's error log: an httpx transport error chained
under a Telegram error carried the token past the redactor, so the error handler logs exception
type names and stack frames only, for the whole chain, and never any exception's text.

Bot PR 2b adds the update_id claim: AC-3 (an update_id already claimed never reaches a handler
again, including after a restart on the same database file; a new id's claim row reads back
before any later handler runs; claims older than 48 h are pruned) and AC-2 for it (a claim that
raises stops the update). The claim is `claim(conn, update_id) -> bool` in meals/bot/gate.py: it
refuses an id whose type isn't exactly int (TypeError), then, in one transaction, deletes claims
whose SQLite-written `seen_at` is older than `datetime('now', '-48 hours')` and inserts the id
with `ON CONFLICT(update_id) DO NOTHING`; True only when it inserted the id; committed before it
returns; any error, IntegrityError included, propagates. The gate runs the chat lock first, then
the claim on `BotDeps.gate_conn`. Claims are read back on a connection of their own, so only
committed rows count, and old rows are aged by SQLite (`datetime('now', '-49 hours')`), never
by a timestamp written from Python.
"""

import html
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import AIORateLimiter, ContextTypes, TypeHandler

from meals.contracts import PantryItem
from meals.db import get_db
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

_CLAIM_MISSING: ImportError | None = None
try:
    from meals.bot.gate import claim
except ImportError as exc:  # RED: bot PR 2b's claim; `_require_claim` names it per test
    _CLAIM_MISSING = exc

STRANGER_ID = 9191
GROUP_CHAT_ID = -100777
GATE_ERROR = "gate failure marker 5d2"  # the text of the error the broken gate raises
TRANSPORT_TEXT = "transport marker 9c4"  # R4-1: text only the transport cause's message carries
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
    # reads it. The post-PR review's R4-1: the error handler logs exception type names and stack
    # frames only, never an exception's text, so the log names RuntimeError and shows no GATE_ERROR.
    # The gate raises a bound error, so its frame's source line (`raise error`) doesn't name its
    # type, and only a logged type name can.
    caplog.set_level(logging.DEBUG, logger="meals")
    ran: list[int] = []

    async def broken_gate(update: Update, *args: object, **kwargs: object) -> None:
        ran.append(update.update_id)
        error = RuntimeError(GATE_ERROR)
        raise error

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
    records = [record for record in caplog.records if record.name.split(".")[0] == "meals"]
    logged = [  # caplog formats each record as it arrives, filling exc_text
        (record.levelno, record.getMessage() + (record.exc_text or "")) for record in records
    ]
    assert any(level >= logging.ERROR and "RuntimeError" in text for level, text in logged), (
        "no meals ERROR record names the gate error's type"
    )
    assert [text for _, text in logged if GATE_ERROR in text] == [], (
        "a meals record shows the gate error's text"
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


@pytest.mark.parametrize(
    "echoed",
    [TOKEN, ENCODED_TOKEN, SECRET_ENCODED_TOKEN, SECRET_ESCAPED_TOKEN],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
def test_gate_error_log_never_shows_a_transport_cause(
    echoed: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # AC-1's error log, the post-PR review's R4-1: PTB raises NetworkError for an httpx failure,
    # chained `from` the httpx error (telegram/request/_httpxrequest.py:303). That cause is not a
    # Telegram error, and its text can echo the response, token included. The gate raises that
    # chain here. The update still stops and an ERROR is still logged, but no meals record shows
    # the token in any form, searched once decoded, nor the cause's text (TRANSPORT_TEXT): the
    # error handler logs type names and frames only, for the whole chain. The gate is replaced
    # where build_app reads it, as in the S1 test above.
    caplog.set_level(logging.DEBUG, logger="meals")
    ran: list[int] = []
    status_line = f"{TRANSPORT_TEXT} HTTP/1.1 502 for /bot{echoed}/sendMessage".encode()

    async def leaky_gate(update: Update, *args: object, **kwargs: object) -> None:
        ran.append(update.update_id)
        try:
            raise httpx.RemoteProtocolError(f"illegal status line: {status_line!r}")
        except httpx.RemoteProtocolError as cause:
            raise NetworkError(f"httpx.RemoteProtocolError: {cause}") from cause

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
    assert [text for text in logged if TRANSPORT_TEXT in text] == [], (
        "a meals record shows the transport cause's text"
    )


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


# ── bot PR 2b: the update_id claim (AC-3, and AC-2 when the claim fails) ─────


def _require_claim() -> None:
    if _CLAIM_MISSING is not None:
        pytest.fail(
            "meals.bot.gate.claim is not built yet (PLAN.md Phase 4, bot PR 2b, AC-3: "
            "claim(conn, update_id) -> bool raises TypeError for a non-int id, prunes claims "
            "older than 48 h, then inserts the id, committed before it returns; True only when "
            f"this call inserted it): {_CLAIM_MISSING}",
            pytrace=False,
        )


def _claimed(path: Path) -> list[int]:
    """The update_ids committed to the database at `path`, read on a connection of its own."""
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT update_id FROM telegram_update ORDER BY 1")]


def _seed_claim(conn: sqlite3.Connection, update_id: int, age: str) -> None:
    """A claim row whose seen_at SQLite computes, `age` back from now (e.g. '-49 hours'), in the
    column's own UTC text form: never a timestamp written from Python."""
    conn.execute(
        "INSERT INTO telegram_update (update_id, seen_at) VALUES (?, datetime('now', ?))",
        (update_id, age),
    )
    conn.commit()


def _db_path(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


def test_replayed_update_is_dropped_after_restart(
    tmp_path: Path, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-3: a claimed update_id never reaches a handler again, in the same run or after a restart
    # (a fresh connection on the same database file), because Telegram resends every update whose
    # offset it hasn't confirmed. Update 8 is the control: a new id after the restart still passes.
    path = tmp_path / "pantry.sqlite"
    opener = PantryOpener(FakePantry(sample_pantry_items))
    with closing(get_db(path)) as first_run:
        app, request = build_bot(opener, gate_conn=first_run)
        probe = Probe()
        app.add_handler(TypeHandler(Update, probe), group=1)
        dispatch(app, text_update(app.bot, 7, "/pantry"), text_update(app.bot, 7, "/pantry"))

    assert probe.seen == [7], "a replay in the same run reached a later handler group"
    assert opener.opened == 1 and len(request.sent()) == 1, "/pantry ran twice for one update"

    with closing(get_db(path)) as after_restart:
        app, request = build_bot(opener, gate_conn=after_restart)
        probe = Probe()
        app.add_handler(TypeHandler(Update, probe), group=1)
        dispatch(app, text_update(app.bot, 7, "/pantry"), text_update(app.bot, 8, "/pantry"))

    assert probe.seen == [8], "after a restart the replayed update reached a later handler group"
    assert opener.opened == 2 and len(request.sent()) == 1, "/pantry answered the replay"


def test_claim_is_committed_before_dispatch(
    tmp_path: Path, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-3: a new id's claim row reads back before any later handler runs. The reader uses its
    # own connection, so it sees only what the gate committed: a claim still inside an open
    # transaction, or one written after dispatch, reads back empty.
    path = tmp_path / "pantry.sqlite"
    seen: list[list[int]] = []

    async def reader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        seen.append(_claimed(path))

    with closing(get_db(path)) as gate_conn:
        app, _ = build_bot(PantryOpener(FakePantry(sample_pantry_items)), gate_conn=gate_conn)
        app.add_handler(TypeHandler(Update, reader), group=1)
        dispatch(app, text_update(app.bot, 41, "/pantry"), text_update(app.bot, 42, "/pantry"))

    assert seen == [[41], [41, 42]], f"claims committed when each later handler ran: {seen}"


def test_foreign_chat_writes_no_claim(
    db: sqlite3.Connection, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-3 with AC-1, the seam's order: the chat lock runs first, so a stranger's update never
    # touches the database. Steve's update 4 is the control: it is claimed.
    app, _ = build_bot(PantryOpener(FakePantry(sample_pantry_items)), gate_conn=db)

    dispatch(
        app,
        text_update(app.bot, 3, "/pantry", chat_id=STRANGER_ID),
        text_update(app.bot, 4, "/pantry"),
    )

    assert _claimed(_db_path(db)) == [4], "a stranger's update was claimed (or Steve's wasn't)"


def test_update_reusing_an_expired_claims_id_reaches_handlers(
    db: sqlite3.Connection, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-3 through the gate: after a quiet week Telegram can send a new update whose id matches a
    # claim over 48 h old. The gate claims through claim's prune-then-insert, so Steve's update
    # reaches the later handler instead of being dropped as a replay.
    _seed_claim(db, 601, "-49 hours")
    app, request = build_bot(PantryOpener(FakePantry(sample_pantry_items)), gate_conn=db)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 601, "/pantry"))

    assert probe.seen == [601], "a new update with an expired claim's id was dropped"
    assert len(request.sent()) == 1, "/pantry didn't answer it"


def test_claim_returns_true_only_for_a_new_id(db: sqlite3.Connection) -> None:
    # AC-3: True only when this call inserted the id, committed before it returns. After a week
    # with no updates Telegram picks the next id at random, so a lower id is new too: a
    # high-water mark ("drop anything at or below the last id") would drop Steve's message. A
    # replay that inserts nothing still ends its transaction: one left open would hold the
    # database's write lock against every pantry write until the next claim.
    _require_claim()

    assert claim(db, 700) is True
    assert _claimed(_db_path(db)) == [700], "the claim isn't committed when claim() returns"
    assert claim(db, 700) is False, "a replayed id was claimed twice"
    assert not db.in_transaction, "a replay left the claim's transaction open"
    assert claim(db, 650) is True, "a new id below an earlier one was refused"


def test_old_claims_are_pruned(db: sqlite3.Connection) -> None:
    # AC-3: claims older than 48 h are pruned when a new id is claimed (Telegram keeps an
    # undelivered update for at most 24 h). One row each side of 48 h, aged by SQLite itself.
    _require_claim()
    _seed_claim(db, 501, "-49 hours")
    _seed_claim(db, 502, "-47 hours")

    assert claim(db, 503) is True

    assert _claimed(_db_path(db)) == [502, 503], "the 48 h prune kept or dropped the wrong rows"


def test_old_claim_is_pruned_before_its_id_is_claimed_again(db: sqlite3.Connection) -> None:
    # AC-3, the prune runs BEFORE the insert: after a quiet week Telegram can reuse an id whose
    # claim is over 48 h old. Insert-then-prune finds the old row (nothing inserted, False) and
    # drops Steve's new message as a replay.
    _require_claim()
    _seed_claim(db, 601, "-49 hours")

    assert claim(db, 601) is True, "a new update with an expired claim's id was dropped"

    # The new claim's own seen_at is SQLite's UTC text from the last hour: not the expired row's,
    # and not a Python timestamp ('T' separator, or local time), which compares wrongly as text.
    with closing(sqlite3.connect(_db_path(db))) as conn:
        fresh = conn.execute(
            "SELECT seen_at BETWEEN datetime('now', '-1 hours') AND datetime('now')"
            " FROM telegram_update WHERE update_id = 601"
        ).fetchone()
    assert fresh == (1,), "update 601's claim doesn't carry a fresh SQLite seen_at"


@pytest.mark.parametrize("update_id", [None, "7", 7.0, True], ids=["none", "str", "float", "bool"])
def test_claim_refuses_a_non_int_id(update_id: Any, db: sqlite3.Connection) -> None:
    # AC-3, the contracts lane's security review of #35: update_id is the table's rowid alias, so
    # a None insert gets a fresh id (max + 1) and "succeeds" every time, and SQLite turns "7" or
    # 7.0 into 7. claim refuses anything whose type isn't exactly int (bool included) with
    # TypeError, and writes nothing.
    _require_claim()

    with pytest.raises(TypeError):
        claim(db, update_id)

    assert _claimed(_db_path(db)) == [], f"claim({update_id!r}) wrote a row"


def test_claim_error_propagates(db: sqlite3.Connection) -> None:
    # AC-2 with AC-3, the contracts lane's security review of #35: an IntegrityError from the
    # claim is a failure, never "duplicate, skip". The trigger makes SQLite refuse the insert of
    # a new id the way a constraint does. The failed claim's transaction (its prune included)
    # doesn't stay open holding the database's write lock.
    _require_claim()
    db.execute(
        "CREATE TRIGGER refuse_claims BEFORE INSERT ON telegram_update"
        " BEGIN SELECT RAISE(ABORT, 'claims refused'); END"
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        claim(db, 801)

    assert not db.in_transaction, "the failed claim left its transaction open"


@pytest.mark.parametrize(
    ("broken", "error_type"),
    [("refusing_trigger", "IntegrityError"), ("unmigrated", "OperationalError")],
)
def test_claim_failure_stops_every_handler(
    broken: str,
    error_type: str,
    db: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # AC-2 with AC-3: when the claim raises, the update stops (fail closed) and the failure is
    # logged as an error (the app's error handler logs exception type names), not let through and
    # not dropped quietly as a replay. Steve's own /pantry, the strongest case. The database
    # refuses the insert (an IntegrityError, as above), or has no telegram_update table (an
    # OperationalError).
    caplog.set_level(logging.DEBUG, logger="meals")
    if broken == "refusing_trigger":
        db.execute(
            "CREATE TRIGGER refuse_claims BEFORE INSERT ON telegram_update"
            " BEGIN SELECT RAISE(ABORT, 'claims refused'); END"
        )
        db.commit()
        gate_conn = db
    else:
        gate_conn = sqlite3.connect(":memory:")
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener, gate_conn=gate_conn)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 9, "/pantry"))

    assert opener.opened == 0, "the pantry was opened after the claim failed"
    assert probe.seen == [], "a later handler group ran after the claim failed"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"
    logged = [
        (record.levelno, record.getMessage() + (record.exc_text or ""))
        for record in caplog.records
        if record.name.split(".")[0] == "meals"
    ]
    assert any(level >= logging.ERROR and error_type in text for level, text in logged), (
        f"no meals ERROR record names the claim's {error_type}"
    )
