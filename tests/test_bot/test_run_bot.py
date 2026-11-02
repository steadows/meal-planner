"""meals.bot.handlers.run_bot: refuses to start without what it needs (AC-16), and polls without
dropping the messages that arrived while it was down.

Authority: PLAN.md, Phase 4 (the bot, locked to Steve's chat ID); ADR-0001, Decision (the bot is
one long-running process that handles chat); and the bot PR's Acceptance → proof table: AC-16 (no
TELEGRAM_BOT_TOKEN, no TELEGRAM_ALLOWED_CHAT_ID, or a PANTRY_DB path that doesn't exist: refuse
before polling, name what's missing, never print the token, create no database file; run_bot
builds real deps from get_settings() and runs polling) and AC-1 (the lock is on
TELEGRAM_ALLOWED_CHAT_ID). The team-lead's 2a brief (2026-11-02): a refusal is a RuntimeError, and
run_polling is called without drop_pending_updates=True.

The pre-PR review's FIX NOWs and the team-lead's repair brief (2026-11-02) add: F1, a PANTRY_DB
deleted after startup is not re-created by /pantry, which replies its one-line failure; F2, a
PANTRY_DB that isn't a pantry database this code can open (not SQLite, or a newer schema) is
refused before polling and left as it was; F3,
TELEGRAM_ALLOWED_CHAT_ID=0 is refused like a missing one; F5, run_bot checks the token with
Telegram before polling, and a rejected token is refused without showing it; F6, run_polling asks
Telegram for message and edited_message updates only.

The security review (2026-11-02) adds to AC-16: S3, a negative (group) TELEGRAM_ALLOWED_CHAT_ID is
refused; S2, a token Telegram rejects at run_polling's own start is refused without showing it.

The post-PR review's C2 (2026-11-02) adds to AC-16: a Telegram failure other than a rejected token,
at the check or at run_polling's start, is refused without showing the token. PTB copies an error
body that isn't JSON into the error's text (telegram/request/_baserequest.py:334), so a gateway's
502 page that echoes the request URL carries the token, raw or with ':' percent-encoded as '%3A'.

The post-PR review's R3-1 (2026-11-02) adds to C2: that text is untrusted and can encode the token
any way, so the same failures with the secret's '-' as '%2D' or '&#45;' are refused without showing
the token either, and what is shown is searched once percent-decoded and HTML-unescaped too.

The post-PR review's R4-2 (2026-11-02) adds to AC-16: a reply PTB can't parse raises a plain
TypeError, not a Telegram error, and its chain can carry the token. run_bot turns ANY exception from
the getMe check or from run_polling into a refusal naming the step, without the exception's text or
chain; a rejected token keeps its own refusal.

Settings come from env vars only, with the project .env switched off. `Application.run_polling` is
replaced by a recorder, so nothing polls, and conftest's guard fails anything that would reach
Telegram. A test whose startup gets as far as the token check routes PTB's HTTP client to a
RecordingRequest (`telegram`), or, for F5, to a Telegram that rejects the token.

Limit (F5): telegram.* DEBUG records go unchecked; wiring must never log telegram.* at DEBUG.

Bot PR 2b adds AC-3 to startup: run_bot opens the gate's claim connection on PANTRY_DB. A test that
sends updates to the polled app does so through `serve`, inside the replaced run_polling, so a
run_bot that closes that connection when polling ends is still served correctly. The pre-PR code
review's CR-2 adds to AC-16: that open failing is refused like the first open failing.
"""

import html
import inspect
import json
import logging
import re
import sqlite3
import traceback
from collections.abc import Callable
from contextlib import closing, suppress
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from telegram.error import InvalidToken, NetworkError
from telegram.ext import Application
from telegram.request import HTTPXRequest

from meals.config import Settings
from meals.db import MIGRATIONS, get_db
from test_bot.conftest import ALLOWED_CHAT_ID, AnyApp, RecordingRequest, dispatch, text_update

with suppress(
    ImportError
):  # RED: conftest's `_bot_is_built` fails each test, naming what's missing
    from meals.bot import handlers

SECRET = "TEST-token-abc"  # a bot token's secret is the part after the colon
TOKEN = f"123456:{SECRET}"
ENCODED_TOKEN = TOKEN.replace(":", "%3A")  # the token as a percent-encoded URL carries it
SECRET_ENCODED_TOKEN = ENCODED_TOKEN.replace("-", "%2D")  # R3-1: encoded inside the secret too
SECRET_ESCAPED_TOKEN = TOKEN.replace("-", "&#45;")  # R3-1: HTML-escaped inside the secret
GATEWAY_PAGE = "<html><title>502 Bad Gateway</title><p>No answer from {url}</p></html>"
TELEGRAM_FAILED = "(?i)telegram|getme"  # the refusal says Telegram (or getMe) failed
GROUP_CHAT_ID = -100777
MARKER = "zaatar marker 7f3"  # a pantry row only the test's PANTRY_DB holds
PREFIX = "Couldn't read the pantry: "

Polls = list[tuple[AnyApp, dict[str, Any]]]
Serve = Callable[[Callable[[AnyApp], None]], None]


def _shows_secret(text: str) -> bool:
    """Whether `text` shows the token's secret half, as written or once percent-decoded and
    HTML-unescaped (R3-1: Telegram's error text is untrusted and can encode the token any way)."""
    return SECRET in text or SECRET in unquote(html.unescape(text))


@pytest.fixture
def polls(monkeypatch: pytest.MonkeyPatch) -> Polls:
    """`Application.run_polling` replaced: each call records (the app, the arguments it was given,
    by parameter name) and returns at once."""
    calls: Polls = []
    signature = inspect.signature(Application.run_polling)

    def record(self: AnyApp, *args: Any, **kwargs: Any) -> None:
        calls.append((self, dict(signature.bind(self, *args, **kwargs).arguments)))

    monkeypatch.setattr(Application, "run_polling", record)
    return calls


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Serve:
    """Calls run_bot with `Application.run_polling` replaced by the test's `scenario(app)`, so the
    test's updates reach the app while run_bot is still polling, as Telegram's would. Bot PR 2b:
    run_bot's gate connection may close once polling ends, so nothing is sent to the app after."""

    def run(scenario: Callable[[AnyApp], None]) -> None:
        served: list[AnyApp] = []

        def polling(self: AnyApp, *args: Any, **kwargs: Any) -> None:
            served.append(self)
            scenario(self)

        monkeypatch.setattr(Application, "run_polling", polling)
        handlers.run_bot()
        assert len(served) == 1, f"run_polling was called {len(served)} times"

    return run


@pytest.fixture
def bot_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_settings: None) -> Path:
    """Settings a bot can start with: the token, Steve's chat id, and a migrated (empty) pantry
    database at the returned path."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)  # never the project's .env
    path = tmp_path / "pantry.sqlite"
    with closing(get_db(path)):
        pass
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", str(ALLOWED_CHAT_ID))
    monkeypatch.setenv("PANTRY_DB", str(path))
    return path


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> RecordingRequest:
    """PTB's own HTTP client answered by a RecordingRequest, whose getMe accepts the token."""
    recorder = RecordingRequest()

    async def answer(self: HTTPXRequest, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        return await recorder.do_request(*args, **kwargs)

    monkeypatch.setattr(HTTPXRequest, "do_request", answer)
    return recorder


@pytest.mark.usefixtures("bot_env")
def test_refuses_without_token(polls: Polls, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        handlers.run_bot()

    assert polls == [], "run_bot polled before refusing"


@pytest.mark.usefixtures("bot_env")
def test_refuses_without_chat_id(polls: Polls, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID")

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_CHAT_ID"):
        handlers.run_bot()

    assert polls == [], "run_bot polled before refusing"


@pytest.mark.usefixtures("bot_env")
def test_refuses_zero_chat_id(polls: Polls, monkeypatch: pytest.MonkeyPatch) -> None:
    # F3: no chat has id 0; a bot locked to it would start and then drop every update.
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "0")

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_CHAT_ID"):
        handlers.run_bot()

    assert polls == [], "run_bot polled before refusing"


@pytest.mark.parametrize("chat_id", [-1, -1001234567890], ids=["minus_one", "supergroup"])
@pytest.mark.usefixtures("bot_env", "telegram")
def test_refuses_group_chat_id(chat_id: int, polls: Polls, monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-16, the security review's S3: private chat ids are positive, group and supergroup ids
    # negative.
    # The lock compares only the chat id, so a bot locked to a group serves every member of it.
    # -1 is the negative id nearest zero; supergroup ids look like the second. getMe is answered,
    # so the refusal may come before or after the token check, as long as it is before polling.
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", str(chat_id))

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_CHAT_ID") as raised:
        handlers.run_bot()

    assert polls == [], "run_bot polled before refusing"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert SECRET not in shown, "the token is in the refusal or the chain printed with it"


@pytest.mark.usefixtures("bot_env")
def test_refuses_missing_pantry_db(
    polls: Polls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing database is refused, not created: an empty pantry mustn't pass for a real one.
    # The path's directory exists, so a plain sqlite3.connect or get_db would create the file.
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("PANTRY_DB", str(missing))

    with pytest.raises(RuntimeError, match=f"PANTRY_DB|{re.escape(str(missing))}"):
        handlers.run_bot()

    assert not missing.exists(), "the refusal created the pantry database"
    assert polls == [], "run_bot polled before refusing"


@pytest.mark.parametrize("content", ["not_sqlite", "newer_schema"])
def test_refuses_unreadable_pantry_db(content: str, bot_env: Path, polls: Polls) -> None:
    # F2: the file exists but isn't a pantry database this code can open: a few bytes of text, or
    # a schema newer than this checkout's (meals.db refuses to write to it). Refused before
    # polling, and left exactly as it was.
    if content == "not_sqlite":
        bot_env.write_bytes(b"not a pantry database\n" * 3)
    else:
        with closing(sqlite3.connect(bot_env)) as conn:
            conn.execute("PRAGMA user_version = 999")
    before = bot_env.read_bytes()

    with pytest.raises(RuntimeError, match=f"PANTRY_DB|{re.escape(str(bot_env))}"):
        handlers.run_bot()

    assert bot_env.read_bytes() == before, "the refusal changed the pantry database file"
    assert polls == [], "run_bot polled before refusing"


@pytest.mark.usefixtures("telegram")
def test_refuses_when_the_gate_connection_cannot_open(
    bot_env: Path, polls: Polls, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CR-2 (the pre-PR code review), AC-16 (a refusal names the step): run_bot opens PANTRY_DB
    # twice at startup, #38's check that it opens, then the gate's claim connection. The first
    # open works and the second raises sqlite3's OperationalError. run_bot refuses as it does when
    # the first fails, "Could not open PANTRY_DB" with the path, not with the raw sqlite3 error,
    # and never polls. getMe is answered, so the refusal may come before or after the token check.
    opened: list[object] = []

    def second_open_fails(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append(args)
        if len(opened) == 2:
            raise sqlite3.OperationalError("unable to open database file")
        return get_db(*args, **kwargs)

    monkeypatch.setattr(handlers, "get_db", second_open_fails)  # handlers' `from meals.db import`

    with pytest.raises(RuntimeError) as raised:
        handlers.run_bot()

    assert len(opened) == 2, f"run_bot opened PANTRY_DB {len(opened)} times at startup"
    refusal = str(raised.value)
    assert refusal.startswith("Could not open PANTRY_DB") and str(bot_env) in refusal, refusal
    assert polls == [], "run_bot polled after the gate connection failed to open"


@pytest.mark.usefixtures("telegram")
def test_pending_migrations_run_at_startup(bot_env: Path, polls: Polls) -> None:
    # F2: startup brings an older pantry database's schema up to date, so no migration is left to
    # run inside a /pantry on the event loop. The database is at v1 when run_bot starts.
    bot_env.unlink()
    with closing(sqlite3.connect(bot_env)) as conn:
        for statement in MIGRATIONS[0]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 1")

    handlers.run_bot()

    assert len(polls) == 1, f"run_polling was called {len(polls)} times"
    with closing(sqlite3.connect(bot_env)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == len(MIGRATIONS), f"the pantry database is still at v{version} after startup"


@pytest.mark.parametrize("missing", ["TELEGRAM_ALLOWED_CHAT_ID", "PANTRY_DB"])
@pytest.mark.usefixtures("bot_env", "polls")
def test_refusal_never_shows_the_token(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The token is set and something else is missing. Its secret part appears nowhere: not in the
    # error, the exceptions chained to it, the log, or anything printed.
    caplog.set_level(logging.DEBUG)
    if missing == "PANTRY_DB":
        monkeypatch.setenv("PANTRY_DB", str(tmp_path / "missing.sqlite"))
    else:
        monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError) as raised:
        handlers.run_bot()

    printed = capsys.readouterr()
    assert SECRET not in "".join(traceback.format_exception(raised.value))
    assert SECRET not in caplog.text
    assert SECRET not in printed.out + printed.err


@pytest.mark.usefixtures("bot_env")
def test_rejected_token_is_refused_without_showing_it(
    polls: Polls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # F5: Telegram answers getMe with 401. PTB's own InvalidToken carries the whole token in its
    # text (_bot.py:867) and its retry loop logs it (ext/_utils/networkloop.py:183), so the token
    # must appear nowhere in the refusal as Python prints it (the chain it shows included), in no
    # log record (message or traceback), and in nothing printed. PTB's own DEBUG records
    # (telegram.*) are left out: PTB logs its API URL, token included, at DEBUG whenever a Bot is
    # built (_bot.py:342), and whether telegram.* DEBUG is ever recorded is bot logging setup,
    # wiring P4.1's (the bot PR's Out of scope).
    caplog.set_level(logging.DEBUG)
    unauthorized = json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"})

    async def reject(self: HTTPXRequest, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        return 401, unauthorized.encode()

    monkeypatch.setattr(HTTPXRequest, "do_request", reject)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN") as raised:
        handlers.run_bot()

    assert polls == [], "run_bot polled with a rejected token"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert SECRET not in shown, "the token is in the refusal or the chain printed with it"
    for record in caplog.records:  # caplog formats each record as it arrives, filling exc_text
        if record.levelno <= logging.DEBUG and record.name.startswith("telegram"):
            continue
        logged = record.getMessage() + (record.exc_text or "")
        assert SECRET not in logged, f"a {record.name} log record has the token"
    printed = capsys.readouterr()
    assert SECRET not in printed.out + printed.err, "the token was printed"


@pytest.mark.usefixtures("bot_env", "telegram")
def test_token_rejected_at_polling_start_is_refused_without_showing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC-16, the security review's S2: getMe accepts the token at the check, then Telegram rejects
    # it when run_polling initializes the bot again. PTB's Bot.initialize raises InvalidToken with
    # the whole token in its text (_bot.py:867-868) and it comes out of run_polling; the replaced
    # run_polling raises that error. Log records go unchecked here: PTB's retry loop logs it, token
    # included, at ERROR (ext/_utils/networkloop.py:183), and that is bot logging setup, wiring
    # P4.1's (the bot PR's Out of scope; the security review leaves it to wiring).
    started: list[AnyApp] = []

    def rejected(self: AnyApp, *args: Any, **kwargs: Any) -> None:
        started.append(self)
        raise InvalidToken(f"The token `{TOKEN}` was rejected by the server.")

    monkeypatch.setattr(Application, "run_polling", rejected)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN") as raised:
        handlers.run_bot()

    assert len(started) == 1, f"run_polling was called {len(started)} times"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert SECRET not in shown, "the token is in the refusal or the chain printed with it"


@pytest.mark.parametrize(
    "echoed",
    [TOKEN, ENCODED_TOKEN, SECRET_ENCODED_TOKEN, SECRET_ESCAPED_TOKEN],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
@pytest.mark.usefixtures("bot_env")
def test_telegram_failure_at_the_token_check_never_shows_the_token(
    echoed: str,
    polls: Polls,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # AC-16, the post-PR review's C2: getMe fails with something other than a rejected token. A
    # gateway answers 502 with an HTML page that echoes the request URL, token included; PTB copies
    # a body that isn't JSON into the error's text (_baserequest.py:334) and raises NetworkError.
    # run_bot refuses before polling, and the token appears nowhere in the refusal, the chain
    # printed with it, or a meals log record: not raw, not with ':' as '%3A' (a redactor that
    # replaces only the exact token misses that one), and not the secret half alone. R3-1: nor
    # with the secret's '-' as '%2D' or '&#45;' (a redactor that replaces only the secret as
    # written misses those), so what is shown is also searched once decoded.
    caplog.set_level(logging.DEBUG, logger="meals")
    page = GATEWAY_PAGE.format(url=f"https://api.telegram.org/bot{echoed}/getMe").encode()

    async def gateway(self: HTTPXRequest, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        return 502, page

    monkeypatch.setattr(HTTPXRequest, "do_request", gateway)

    with pytest.raises(RuntimeError, match=TELEGRAM_FAILED) as raised:
        handlers.run_bot()

    assert polls == [], "run_bot polled after getMe failed"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert not _shows_secret(shown), "the refusal shows the token"
    logged = [
        record.getMessage() + (record.exc_text or "")
        for record in caplog.records
        if record.name.split(".")[0] == "meals"
    ]
    assert [text for text in logged if _shows_secret(text)] == [], "a meals record has the token"


@pytest.mark.parametrize(
    "echoed",
    [TOKEN, ENCODED_TOKEN, SECRET_ENCODED_TOKEN, SECRET_ESCAPED_TOKEN],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
@pytest.mark.usefixtures("bot_env", "telegram")
def test_telegram_failure_at_polling_start_never_shows_the_token(
    echoed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-16, the post-PR review's C2: getMe accepts the token at the check, then run_polling fails
    # with the NetworkError PTB raises for a gateway's 502 page that echoes the request URL
    # (_baserequest.py:334). The replaced run_polling raises that error. The refusal and the chain
    # printed with it show no form of the token, R3-1's '%2D' and '&#45;' forms included, searched
    # as written and once decoded. Log records go unchecked, as in the S2 test above: PTB's own
    # network-loop logging is bot logging setup, wiring P4.1's.
    started: list[AnyApp] = []
    page = GATEWAY_PAGE.format(url=f"https://api.telegram.org/bot{echoed}/getUpdates").encode()

    def failed(self: AnyApp, *args: Any, **kwargs: Any) -> None:
        started.append(self)
        raise NetworkError(f"Bad Gateway (502). Parsing the server response {page!r} failed")

    monkeypatch.setattr(Application, "run_polling", failed)

    with pytest.raises(RuntimeError, match=TELEGRAM_FAILED) as raised:
        handlers.run_bot()

    assert len(started) == 1, f"run_polling was called {len(started)} times"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert not _shows_secret(shown), "the refusal shows the token"


@pytest.mark.parametrize(
    "echoed",
    [TOKEN, ENCODED_TOKEN, SECRET_ENCODED_TOKEN, SECRET_ESCAPED_TOKEN],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
@pytest.mark.usefixtures("bot_env")
def test_malformed_getme_reply_never_shows_the_token(
    echoed: str,
    polls: Polls,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # AC-16, the post-PR review's R4-2: getMe answers 200 with a User that has no required field
    # and one unexpected field named after the token. PTB 22.8 builds the User with that field as
    # a keyword (telegram/_telegramobject.py:397), and its fallback raises a plain TypeError
    # ("User.__init__() missing 3 required positional arguments ...") while handling the first
    # TypeError ("... got an unexpected keyword argument '<the token>'"), so the token is in the
    # chain, not a Telegram error. run_bot refuses before polling, naming getMe, and the token
    # appears nowhere in the refusal, the chain printed with it, or a meals log record, searched
    # as written and once decoded.
    caplog.set_level(logging.DEBUG, logger="meals")
    reply = json.dumps({"ok": True, "result": {echoed: 1}}).encode()

    async def malformed(self: HTTPXRequest, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        return 200, reply

    monkeypatch.setattr(HTTPXRequest, "do_request", malformed)

    with pytest.raises(RuntimeError, match="(?i)getme") as raised:
        handlers.run_bot()

    assert polls == [], "run_bot polled after getMe's reply couldn't be read"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert not _shows_secret(shown), "the refusal shows the token"
    logged = [
        record.getMessage() + (record.exc_text or "")
        for record in caplog.records
        if record.name.split(".")[0] == "meals"
    ]
    assert [text for text in logged if _shows_secret(text)] == [], "a meals record has the token"


@pytest.mark.parametrize(
    "echoed",
    [TOKEN, ENCODED_TOKEN, SECRET_ENCODED_TOKEN, SECRET_ESCAPED_TOKEN],
    ids=["raw", "percent_encoded", "secret_percent_encoded", "secret_html_escaped"],
)
@pytest.mark.usefixtures("bot_env", "telegram")
def test_malformed_reply_at_polling_start_never_shows_the_token(
    echoed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-16, the post-PR review's R4-2: getMe accepts the token at the check, then run_polling's
    # start fails with a plain TypeError, as PTB raises for a reply it can't parse (the test
    # above). Here the error's own text carries the token, and so does the error it is chained
    # from. The replaced run_polling raises it. run_bot refuses, naming polling, and the refusal
    # and the chain printed with it show no form of the token, searched as written and once
    # decoded. Log records go unchecked, as in the S2 test above.
    started: list[AnyApp] = []

    def failed(self: AnyApp, *args: Any, **kwargs: Any) -> None:
        started.append(self)
        unexpected = TypeError(f"User.__init__() got an unexpected keyword argument '{echoed}'")
        raise TypeError(f"Couldn't build a User from the reply's '{echoed}'") from unexpected

    monkeypatch.setattr(Application, "run_polling", failed)

    with pytest.raises(RuntimeError, match="(?i)polling") as raised:
        handlers.run_bot()

    assert len(started) == 1, f"run_polling was called {len(started)} times"
    shown = str(raised.value) + "".join(traceback.format_exception(raised.value))
    assert not _shows_secret(shown), "the refusal shows the token"


@pytest.mark.usefixtures("bot_env", "telegram")
def test_polls_without_dropping_pending_updates(polls: Polls) -> None:
    # Messages Steve sent while the bot was down must still arrive when it starts. F6: polling
    # names the update kinds the bot reads, so no filter an earlier run left on Telegram's side
    # applies.
    handlers.run_bot()

    assert len(polls) == 1, f"run_polling was called {len(polls)} times"
    _, arguments = polls[0]
    assert not arguments.get("drop_pending_updates"), f"run_polling got {arguments}"
    allowed = arguments.get("allowed_updates")
    assert allowed is not None, "run_polling got no allowed_updates"
    assert sorted(allowed) == ["edited_message", "message"], f"allowed_updates={allowed!r}"


def test_polled_app_answers_the_configured_chat_from_the_pantry_db(
    bot_env: Path, serve: Serve, telegram: RecordingRequest
) -> None:
    # AC-1 locks the bot to the TELEGRAM_ALLOWED_CHAT_ID setting; AC-16 has run_bot serve /pantry
    # from the PANTRY_DB setting. The app run_bot polls gets /pantry from
    # Steve's chat and from a group he writes in: only his chat is answered, from the row put in
    # that database.
    with closing(get_db(bot_env)) as conn:
        conn.execute(
            "INSERT INTO pantry_item (name, aliases, category, status) VALUES (?, ?, ?, ?)",
            (MARKER, "[]", "staple", "buy_next_time"),
        )
        conn.commit()

    def scenario(app: AnyApp) -> None:
        dispatch(
            app,
            text_update(app.bot, 1, "/pantry"),
            text_update(app.bot, 2, "/pantry", chat_id=GROUP_CHAT_ID, from_id=ALLOWED_CHAT_ID),
        )

    serve(scenario)

    sent = telegram.sent()
    assert [params["chat_id"] for params in sent] == [ALLOWED_CHAT_ID], sent
    assert MARKER in sent[0]["text"], sent[0]["text"]


def test_vanished_pantry_db_is_not_recreated(
    bot_env: Path, serve: Serve, telegram: RecordingRequest
) -> None:
    # F1 (AC-16 "creates no database file", after startup): PANTRY_DB is deleted while the bot
    # runs. /pantry replies its one-line failure and doesn't make a new, empty pantry.
    def scenario(app: AnyApp) -> None:
        bot_env.unlink()
        dispatch(app, text_update(app.bot, 1, "/pantry"))

    serve(scenario)

    sent = telegram.sent()
    assert len(sent) == 1 and sent[0]["text"].startswith(PREFIX), sent
    assert "\n" not in sent[0]["text"] and "parse_mode" not in sent[0], sent
    assert not bot_env.exists(), "/pantry re-created the pantry database"


def test_gate_claims_updates_in_the_pantry_db(
    bot_env: Path, serve: Serve, telegram: RecordingRequest
) -> None:
    # AC-3, bot PR 2b: run_bot opens the gate's connection on PANTRY_DB, so an update's claim is
    # in the shared database and outlives a restart. The polled app gets /pantry as update 31;
    # its claim row reads back from PANTRY_DB on a connection of the test's own.
    serve(lambda app: dispatch(app, text_update(app.bot, 31, "/pantry")))

    assert len(telegram.sent()) == 1, "the polled app didn't answer /pantry"
    with closing(sqlite3.connect(bot_env)) as conn:
        claimed = [row[0] for row in conn.execute("SELECT update_id FROM telegram_update")]
    assert claimed == [31], f"claims in PANTRY_DB: {claimed}"
