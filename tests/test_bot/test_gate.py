"""meals.bot's gate (the chat lock) and how build_app registers it.

Authority: the phase4-bot seam map (.context/seams/phase4-bot.md): AC-1 (an update from another
chat, or with no chat: no later handler runs, nothing is written, nothing is replied), D4 (one
`TypeHandler(Update, gate, block=True)` in group -1, allowlist first, ApplicationHandlerStop), the
reuse rows for `AIORateLimiter(max_retries=3)` and `concurrent_updates=False`, the `handlers.py`
seam, and "PR split (rev 3)" (2a: the allowlist only, no claim).

Each dispatch test builds the real app (conftest) and adds a probe handler in group 1, after
/pantry's group 0: the probe runs only if nothing stopped the update before it.
`test_allowed_chat_reaches_later_groups` is the control that shows each "nothing happened"
observation in the other two would have seen something.
"""

import pytest
from telegram import Update
from telegram.ext import AIORateLimiter, ContextTypes, TypeHandler

from meals.contracts import PantryItem
from meals.fakes import FakePantry
from test_bot.conftest import ALLOWED_CHAT_ID, PantryOpener, build_bot, dispatch, text_update

STRANGER_ID = 9191
GROUP_CHAT_ID = -100777


class Probe:
    """A handler for a later group: records the update_id of each update that reaches it."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def __call__(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.seen.append(update.update_id)


@pytest.mark.parametrize(
    ("chat_id", "from_id"),
    [(STRANGER_ID, STRANGER_ID), (GROUP_CHAT_ID, ALLOWED_CHAT_ID)],
    ids=["stranger_in_their_own_chat", "steve_in_another_chat"],
)
def test_foreign_chat_stops_every_handler(
    chat_id: int, from_id: int, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # AC-1. The lock is on the chat, not the sender: Steve writing from another chat is still
    # another chat, so a gate keyed on the sender's id fails the second case.
    opener = PantryOpener(FakePantry(sample_pantry_items))
    app, request = build_bot(opener)
    probe = Probe()
    app.add_handler(TypeHandler(Update, probe), group=1)

    dispatch(app, text_update(app.bot, 1, "/pantry", chat_id=chat_id, from_id=from_id))

    assert probe.seen == [], "a later handler group ran for a foreign chat"
    assert opener.opened == 0, "the pantry was opened for a foreign chat"
    assert [name for name, _ in request.calls] == ["getMe"], "the bot called Telegram about it"


def test_update_without_chat_is_dropped(sample_pantry_items: tuple[PantryItem, ...]) -> None:
    # AC-1 "or with no chat"; D4 "a missing chat is rejected". A button press on an inline message
    # has a sender, here Steve himself, but no chat.
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


def test_app_is_sequential_and_rate_limited(fake_pantry: FakePantry) -> None:
    # Seam map reuse rows: flood-control retries via AIORateLimiter(max_retries=3), and updates
    # processed one at a time (concurrent_updates=False), so the gate sees them in arrival order.
    app, _ = build_bot(PantryOpener(fake_pantry))

    limiter = app.bot.rate_limiter
    assert isinstance(limiter, AIORateLimiter), f"rate limiter is {limiter!r}"
    assert limiter._max_retries == 3  # PTB keeps max_retries only in this attribute
    assert app.concurrent_updates == 1


def test_gate_is_blocking_in_group_minus_one(fake_pantry: FakePantry) -> None:
    # D4: one TypeHandler(Update, gate, block=True) in group -1, and the allowlist runs first, so no
    # group comes before it. block=True explicitly: PTB's default is DEFAULT_TRUE, which a
    # Defaults(block=False) would turn into a non-blocking gate whose stop can't stop anything.
    app, _ = build_bot(PantryOpener(fake_pantry))

    assert min(app.handlers) == -1, f"handler groups: {sorted(app.handlers)}"
    assert len(app.handlers[-1]) == 1, f"group -1 holds {app.handlers[-1]}"
    gate = app.handlers[-1][0]
    assert isinstance(gate, TypeHandler) and gate.type is Update, f"the gate is {gate!r}"
    assert gate.block is True
