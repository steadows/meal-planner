"""/pantry: what Steve sees when he asks what's in the pantry (AC-13, the /pantry part, PR 2a).

Authority: the phase4-bot seam map (.context/seams/phase4-bot.md): AC-13 ("/pantry lists items
with status (split at Telegram's limit)") and the "/pantry text (2a)" paragraph under "PR split
(rev 3)": two lines, `On the list: <names>` (status buy_next_time) then `Have: <names>`, names in
`list_items()` order joined by ", ", `nothing` for an empty group; an empty pantry gets
`The pantry is empty. Run the seed loader first.`; long text goes through
`background.split_message`; it runs inline, with no "on it" ack. The team-lead's 2a brief
(2026-11-02) adds: one `open_pantry()` per command; every reply plain text; a failed open or read
replies one line, `Couldn't read the pantry: ` + `background.first_line(exc)`, the whole line cut
to `background.ERROR_MAX_CHARS`.

Expected texts are written out by hand from the conftest pantry (ids 1-6: olive oil, rice, butter
[buy_next_time], tahini, eggs, chicken nuggets). Every case sends /pantry from Steve's chat through
PTB's real dispatch and reads what reached the wire.
"""

import sqlite3
from typing import Any

import pytest
from telegram.constants import MessageLimit

from meals.background import ERROR_MAX_CHARS, split_message
from meals.contracts import PantryItem, PantryStatus
from meals.fakes import FakePantry
from test_bot.conftest import PantryOpener, build_bot, dispatch, text_update

PREFIX = "Couldn't read the pantry: "
EMPTY_TEXT = "The pantry is empty. Run the seed loader first."


def _ask(opener: PantryOpener) -> list[dict[str, Any]]:
    """Send /pantry from Steve's chat; return the parameters of each message the bot sent."""
    app, request = build_bot(opener)
    dispatch(app, text_update(app.bot, 1, "/pantry"))
    return request.sent()


def _texts(sent: list[dict[str, Any]]) -> list[str]:
    return [params["text"] for params in sent]


def _flipped(items: tuple[PantryItem, ...], flips: dict[str, PantryStatus]) -> FakePantry:
    pantry = FakePantry(items)
    for name, status in flips.items():
        assert pantry.flip_status(name, status) is not None, name
    return pantry


def _long_items() -> list[PantryItem]:
    """300 items whose /pantry text is three Telegram messages and a bit."""
    return [
        PantryItem(id=n, name=f"pantry item {n:03d} from the long seed list", category="perishable")
        for n in range(1, 301)
    ]


class BrokenPantry(FakePantry):
    """A pantry whose `list_items()` raises `error`."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def list_items(self) -> tuple[PantryItem, ...]:
        raise self._error


def test_pantry_lists_on_the_list_then_have(sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # Olive oil (id 1) joins butter (id 3) on the list: list_items() order puts it first, where
    # alphabetical order wouldn't, and the Have line isn't alphabetical either.
    opener = PantryOpener(_flipped(sample_pantry_items, {"olive oil": "buy_next_time"}))

    sent = _ask(opener)

    assert _texts(sent) == [
        "On the list: olive oil, butter\nHave: rice, tahini, eggs, chicken nuggets"
    ]
    assert (opener.opened, opener.closed) == (1, 1), "one open_pantry() per /pantry, closed after"


@pytest.mark.parametrize(
    ("flips", "expected"),
    [
        (
            {"butter": "have"},
            "On the list: nothing\nHave: olive oil, rice, butter, tahini, eggs, chicken nuggets",
        ),
        (
            {
                "olive oil": "buy_next_time",
                "rice": "buy_next_time",
                "tahini": "buy_next_time",
                "eggs": "buy_next_time",
                "chicken nuggets": "buy_next_time",
            },
            "On the list: olive oil, rice, butter, tahini, eggs, chicken nuggets\nHave: nothing",
        ),
    ],
    ids=["nothing_on_the_list", "nothing_at_home"],
)
def test_pantry_says_nothing_for_an_empty_group(
    flips: dict[str, PantryStatus], expected: str, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    assert _texts(_ask(PantryOpener(_flipped(sample_pantry_items, flips)))) == [expected]


def test_empty_pantry_says_run_the_seed_loader() -> None:
    assert _texts(_ask(PantryOpener(FakePantry()))) == [EMPTY_TEXT]


def test_long_pantry_is_split_at_telegram_limit() -> None:
    # The seam map: long /pantry text goes through background.split_message, so the replies are
    # exactly its chunks of the whole text.
    items = _long_items()
    whole = "On the list: nothing\nHave: " + ", ".join(item.name for item in items)
    assert len(whole) > 3 * MessageLimit.MAX_TEXT_LENGTH  # the case under test

    texts = _texts(_ask(PantryOpener(FakePantry(items))))

    assert texts == split_message(whole)


@pytest.mark.parametrize(
    ("where", "error", "expected"),
    [
        pytest.param(
            "open",
            sqlite3.OperationalError("unable to open database file\nTraceback (most recent call"),
            PREFIX + "unable to open database file",
            id="open_raises",
        ),
        pytest.param(
            "list_items",
            RuntimeError('pantry_item row 7 has no category\n  File "meals/pantry.py", line 240'),
            PREFIX + "pantry_item row 7 has no category",
            id="list_items_raises",
        ),
        pytest.param(
            "list_items",
            sqlite3.DatabaseError("database disk image is malformed: " + "page 4 " * 80),
            (PREFIX + "database disk image is malformed: " + "page 4 " * 80)[:ERROR_MAX_CHARS],
            id="long_line_is_cut",
        ),
        pytest.param(
            "open",
            RuntimeError(""),
            PREFIX + "RuntimeError",  # first_line names the type when the text is blank
            id="blank_error_names_its_type",
        ),
    ],
)
def test_pantry_failure_is_one_plain_line(where: str, error: Exception, expected: str) -> None:
    if where == "open":
        opener = PantryOpener(FakePantry(), error=error)
    else:
        opener = PantryOpener(BrokenPantry(error))

    assert _texts(_ask(opener)) == [expected]


@pytest.mark.parametrize("state", ["listed", "empty", "long", "failing"])
def test_replies_are_plain_text(state: str, sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # 2a brief: every reply is plain text. PTB leaves a None parse_mode off the wire, so any
    # parse_mode seen here (the handler's own, or a Defaults one) is formatting.
    openers = {
        "listed": lambda: PantryOpener(FakePantry(sample_pantry_items)),
        "empty": lambda: PantryOpener(FakePantry()),
        "long": lambda: PantryOpener(FakePantry(_long_items())),
        "failing": lambda: PantryOpener(FakePantry(), error=RuntimeError("database is locked")),
    }

    sent = _ask(openers[state]())

    assert sent, "no reply"
    for params in sent:
        assert "parse_mode" not in params, f"reply sent with parse_mode {params['parse_mode']!r}"
