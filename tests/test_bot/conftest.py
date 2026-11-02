"""The PR 2a test instrument for meals.bot.

Authority: PLAN.md, Phase 4 (the bot, locked to Steve's chat ID), and ADR-0001, Ownership (bot: the
chat-ID allowlist is one pre-dispatch handler in group -1). The instrument: the real PTB Application
runs `process_update` on real `Update`s (`Update.de_json`), with the bot's HTTP replaced by
RecordingRequest, which records each Bot API call and answers it with canned JSON. That is PTB's
real dispatch (group order, ApplicationHandlerStop), which AC-1 in the bot PR's Acceptance → proof
table is a claim about.

No test reaches Telegram: RecordingRequest answers every call, and `_no_telegram` fails any call
that reaches PTB's own HTTP client instead (a `build_app` that ignored `request`, or a `run_bot`
that called Telegram in a test that routed nothing to answer it).

Bot PR 2b adds the gate's update_id claim (AC-3), so `build_bot` passes `BotDeps.gate_conn`.
"""

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from telegram import Bot, Update
from telegram.ext import Application
from telegram.request import BaseRequest, HTTPXRequest, RequestData

from meals.contracts import Pantry, PantryItem, PantryStatus
from meals.db import migrate
from meals.fakes import FakePantry

_MISSING: ImportError | None = None
try:
    from meals.bot.handlers import BotDeps, build_app
except ImportError as exc:  # RED: meals/bot/ isn't written yet; `_bot_is_built` names it per test
    _MISSING = exc

AnyApp = Application[Any, Any, Any, Any, Any, Any]

TOKEN = "123456:TEST-token-abc"
ALLOWED_CHAT_ID = 4242  # Steve's private chat; in a private chat the chat id is the user's id
BOT_USER = {"id": 777, "is_bot": True, "first_name": "Meals", "username": "meals_test_bot"}
MESSAGE_DATE = 1_793_000_000  # a fixed send time, as the Unix timestamp Telegram sends


@pytest.fixture(autouse=True)
def _bot_is_built() -> None:
    if _MISSING is not None:
        pytest.fail(
            "meals.bot.handlers is not built yet (PLAN.md Phase 4, bot PR 2a: BotDeps, "
            f"build_app(token, deps, *, request=None), run_bot()): {_MISSING}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Bot API call that bypasses RecordingRequest fails here instead of reaching Telegram."""

    async def refuse(
        self: HTTPXRequest, url: str, *args: object, **kwargs: object
    ) -> tuple[int, bytes]:
        raise AssertionError(f"a test tried to reach Telegram ({url.rsplit('/', 1)[-1]})")

    monkeypatch.setattr(HTTPXRequest, "do_request", refuse)


class RecordingRequest(BaseRequest):
    """The bot's HTTP in tests. Records each Bot API call as (method, parameters) and answers it:
    getMe with BOT_USER, sendMessage with a Message echoing its chat and text, anything else `true`.

    PTB leaves parameters that are None off the wire, so a reply sent with parse_mode=None shows no
    parse_mode here, and one sent with a parse_mode (its own, or a Defaults one) shows it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def read_timeout(self) -> float | None:
        return None

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: object = None,
        write_timeout: object = None,
        connect_timeout: object = None,
        pool_timeout: object = None,
    ) -> tuple[int, bytes]:
        name = url.rsplit("/", 1)[-1]
        params: dict[str, Any] = {} if request_data is None else dict(request_data.parameters)
        self.calls.append((name, params))
        result: object = True
        if name == "getMe":
            result = BOT_USER
        elif name == "sendMessage":
            result = {
                "message_id": len(self.calls),
                "date": MESSAGE_DATE,
                "chat": {"id": params["chat_id"], "type": "private"},
                "text": params["text"],
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self) -> list[dict[str, Any]]:
        """The parameters of each sendMessage call, in order."""
        return [params for name, params in self.calls if name == "sendMessage"]


class PantryOpener:
    """`BotDeps.open_pantry` for tests: each call opens `pantry`, or raises `error` on entering the
    `with` as a real open does, and counts opens and closes."""

    def __init__(self, pantry: Pantry, error: Exception | None = None) -> None:
        self._pantry = pantry
        self._error = error
        self.opened = 0
        self.closed = 0

    @contextmanager
    def __call__(self) -> Iterator[Pantry]:
        self.opened += 1
        if self._error is not None:
            raise self._error
        try:
            yield self._pantry
        finally:
            self.closed += 1


class LockedPantry(FakePantry):
    """A FakePantry whose `flip_status` raises SQLite's "database is locked", writing nothing, for
    the item named `locked` (found as `get_item` finds it, aliases included), until `locked` is set
    to None. The pre-PR code review's CR-1 (bot PR 2b): a pantry error on one intent."""

    def __init__(self, items: tuple[PantryItem, ...], *, locked: str | None) -> None:
        super().__init__(items)
        self.locked = locked

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        item = self.get_item(name)
        if item is not None and item.name == self.locked:
            raise sqlite3.OperationalError("database is locked")
        return super().flip_status(name, status)


def memory_gate_conn() -> sqlite3.Connection:
    """A migrated in-memory database for the gate's update_id claims (AC-3), for a test that
    doesn't read them back."""
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    return conn


def build_bot(
    opener: PantryOpener, *, gate_conn: sqlite3.Connection | None = None
) -> tuple[AnyApp, RecordingRequest]:
    """`build_app` locked to Steve's chat over `opener`, claiming update_ids in `gate_conn` (a
    fresh in-memory database by default), its HTTP recorded instead of sent."""
    request = RecordingRequest()
    deps = BotDeps(
        allowed_chat_id=ALLOWED_CHAT_ID,
        open_pantry=opener,
        gate_conn=memory_gate_conn() if gate_conn is None else gate_conn,
    )
    return build_app(TOKEN, deps, request=request), request


def text_update(
    bot: Bot,
    update_id: int,
    text: str,
    *,
    chat_id: int = ALLOWED_CHAT_ID,
    from_id: int | None = None,
    kind: str = "message",
    business_connection_id: str | None = None,
) -> Update:
    """A real text-message Update, as Telegram sends it; the sender defaults to the chat's owner. A
    leading `/command` carries the bot_command entity, which is what PTB's CommandHandler matches.
    `kind` is the Update field that carries the message (message, edited_message,
    business_message); `business_connection_id`, when given, is set on the message."""
    command = text.split()[0] if text.startswith("/") else ""
    entities = [{"type": "bot_command", "offset": 0, "length": len(command)}] if command else []
    message: dict[str, Any] = {
        "message_id": update_id,
        "date": MESSAGE_DATE,
        "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "group"},
        "from": {
            "id": chat_id if from_id is None else from_id,
            "is_bot": False,
            "first_name": "S",
        },
        "text": text,
        "entities": entities,
    }
    if business_connection_id is not None:
        message["business_connection_id"] = business_connection_id
    return Update.de_json({"update_id": update_id, kind: message}, bot)


def dispatch(app: AnyApp, *updates: Update) -> None:
    """Start `app`, run each update through PTB's real `process_update` in order, then stop it.
    Background work an update starts runs alongside the next update, as in the bot; PTB's `stop`
    waits for the tasks handlers gave `create_task` (background work included), so every reply
    has been sent when this returns."""

    async def scenario() -> None:
        async with app:
            await app.start()
            try:
                for update in updates:
                    await app.process_update(update)
            finally:
                await app.stop()

    asyncio.run(scenario())
