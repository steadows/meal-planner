"""meals.bot.handlers.run_bot: refuses to start without what it needs (AC-16), and polls without
dropping the messages that arrived while it was down.

Authority: the phase4-bot seam map (.context/seams/phase4-bot.md): AC-16 under "PR split (rev 3)"
(no TELEGRAM_BOT_TOKEN, no TELEGRAM_ALLOWED_CHAT_ID, or a PANTRY_DB path that doesn't exist: refuse
before polling, name what's missing, never print the token, create no database file); the
`handlers.py` seam (run_bot builds real deps from get_settings() and runs polling); AC-1 (the lock
is on TELEGRAM_ALLOWED_CHAT_ID). The team-lead's 2a brief (2026-11-02): a refusal is a
RuntimeError, and run_polling is called without drop_pending_updates=True.

Settings come from env vars only, with the project .env switched off. `Application.run_polling` is
replaced by a recorder, so nothing polls, and conftest's guard fails anything that would reach
Telegram; the one test that dispatches through run_bot's app routes PTB's HTTP client to a
RecordingRequest instead.
"""

import inspect
import logging
import re
import traceback
from contextlib import closing, suppress
from pathlib import Path
from typing import Any

import pytest
from telegram.ext import Application
from telegram.request import HTTPXRequest

from meals.config import Settings
from meals.db import get_db
from test_bot.conftest import ALLOWED_CHAT_ID, AnyApp, RecordingRequest, dispatch, text_update

with suppress(
    ImportError
):  # RED: conftest's `_bot_is_built` fails each test, naming what's missing
    from meals.bot import handlers

SECRET = "TEST-token-abc"  # a bot token's secret is the part after the colon
TOKEN = f"123456:{SECRET}"
GROUP_CHAT_ID = -100777
MARKER = "zaatar marker 7f3"  # a pantry row only the test's PANTRY_DB holds

Polls = list[tuple[AnyApp, dict[str, Any]]]


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
def test_polls_without_dropping_pending_updates(polls: Polls) -> None:
    # Messages Steve sent while the bot was down must still arrive when it starts.
    handlers.run_bot()

    assert len(polls) == 1, f"run_polling was called {len(polls)} times"
    _, arguments = polls[0]
    assert not arguments.get("drop_pending_updates"), f"run_polling got {arguments}"


def test_polled_app_answers_the_configured_chat_from_the_pantry_db(
    bot_env: Path, polls: Polls, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-1 locks the bot to the TELEGRAM_ALLOWED_CHAT_ID setting; the handlers.py seam has run_bot
    # build /pantry's pantry from the PANTRY_DB setting. The app run_bot polls gets /pantry from
    # Steve's chat and from a group he writes in: only his chat is answered, from the row put in
    # that database. For this test only, PTB's own HTTP client answers through a RecordingRequest.
    with closing(get_db(bot_env)) as conn:
        conn.execute(
            "INSERT INTO pantry_item (name, aliases, category, status) VALUES (?, ?, ?, ?)",
            (MARKER, "[]", "staple", "buy_next_time"),
        )
        conn.commit()
    recorder = RecordingRequest()

    async def answer(self: HTTPXRequest, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        return await recorder.do_request(*args, **kwargs)

    monkeypatch.setattr(HTTPXRequest, "do_request", answer)
    handlers.run_bot()
    assert len(polls) == 1, f"run_polling was called {len(polls)} times"
    app, _ = polls[0]

    dispatch(
        app,
        text_update(app.bot, 1, "/pantry"),
        text_update(app.bot, 2, "/pantry", chat_id=GROUP_CHAT_ID, from_id=ALLOWED_CHAT_ID),
    )

    sent = recorder.sent()
    assert [params["chat_id"] for params in sent] == [ALLOWED_CHAT_ID], sent
    assert MARKER in sent[0]["text"], sent[0]["text"]
