"""Free text from Steve's chat: one Claude parse, then the bot's own pantry writes (bot PR 2b).

Authority: PLAN.md, Phase 4 (on-the-go pantry updates by text; the bot repeats back what changed)
and Runtime concurrency; ADR-0001, Decision (chat-sized slow work runs through background.start,
off the event loop); and the bot PR's Acceptance → proof table: AC-4, AC-6, AC-7, AC-8, AC-9,
AC-11 and AC-12. The pre-PR code review's CR-1 adds: a pantry error on one intent loses no other
intent's echo.

Bot PR 2b's design, from that table's seams: Claude only parses. One tool-free
`claude_runner.run(prompt, schema=IntentReply, timeout=PARSE_TIMEOUT_S, web=False)` per message,
Steve's text in the prompt, with PARSE_TIMEOUT_S = 120 so both of the runner's attempts end well
inside background's DEADLINE_S. The bot then applies the pantry_flip intents through the Pantry
Protocol and echoes one line per intent, stating each item's resulting state from what the pantry
returned (so "evoo" echoes as olive oil), "don't know <item>" for an item the pantry doesn't have,
and "a newer message already set <item>" when a message that arrived later wrote it first. A
pantry_flip to still_good or plenty, and every other intent kind, gets a "can't do that yet" line
and writes nothing in 2b: AC-5 is bot PR 2c, and the F2 rule (a still-good echo never quotes
next_ask_on as the next ask date) comes with it. An edited message gets one plain line asking
Steve to send it again as a new message, and nothing else runs. The contracts lane's security
review of #35: the parse passes web=False (the runner's default gives Claude WebSearch and
WebFetch), and a parse failure logs ClaudeRunnerError.reason only, never raw_output or a traceback
(the chained ValidationError quotes Claude's output, which can quote Steve).

"Newer" means arrived later (the team-lead's D5 rulings): Telegram picks update_ids at random
after a quiet week, so the free-text handler takes a ticket from the app's own
`pantry_ops.LatestWrites` (build_app creates one per Application) when the message arrives, and
the write is ordered by that ticket. Every test's app starts with an empty one.

The instrument is conftest's: the real PTB Application over RecordingRequest, where `dispatch`
waits for background work to finish, so the bot's answer to a single message is the last message
sent (background sends its ack first). The one concurrent piece is the AC-8 test's: a
threading.Event, waited on for at most WAIT_S, holds the first message's parse until the second
message has written. `claude_runner.run` is FakeClaudeRunner (tests/conftest.py,
`patched_claude`): it answers with an IntentReply as JSON data and validates it against the
schema the bot passes. The pantry is FakePantry over the conftest items (olive oil [alias evoo],
rice, butter [buy_next_time], tahini, eggs, chicken nuggets), read back after each message.

Phrases checked in replies, casefolded: the seam's own quoted phrases ("can't do that yet",
"don't know", "newer message", "new message"), and ON_THE_LIST for an item whose resulting state
is buy_next_time.
"""

import logging
import threading
import time
from typing import Any

import pytest
from pydantic import ValidationError

from meals import background, claude_runner
from meals.background import DEADLINE_S, ERROR_MAX_CHARS
from meals.contracts import ClaudeRunnerError, PantryFlipArgs, PantryItem, PantryStatus
from meals.fakes import FakeClaudeRunner, FakePantry
from test_bot.conftest import (
    AnyApp,
    LockedPantry,
    PantryOpener,
    RecordingRequest,
    build_bot,
    dispatch,
    text_update,
)

_MISSING: ImportError | None = None
try:
    from meals.bot.intents import IntentReply
except ImportError as exc:  # RED: bot PR 2b's intents; `_send` names it per test
    _MISSING = exc

ON_THE_LIST = "on the list"  # the resulting state buy_next_time, as /pantry already labels it
NOT_YET = "can't do that yet"
UNKNOWN = "don't know"
NEWER = "newer message"
RESEND = "new message"

FIRST_TEXT = "eggs are gone"  # AC-8: the message that arrives first (update 900)
SECOND_TEXT = "we are out of eggs"  # AC-8: the message that arrives second (update 12)
WAIT_S = 5.0  # the longest the first message's parse waits for the second message's write

RAW_MARKER = "raw output marker 6e1"  # only the parse failure's raw_output holds this
CAUSE_MARKER = "cause marker 3b8"  # only the ValidationError it is chained from quotes this
REASON_HEAD = "output failed IntentReply validation"
REASON = (  # longer than ERROR_MAX_CHARS, so the reply must be cut
    f"{REASON_HEAD} (1 error(s); first at intents.0.PantryFlipIntent.args.to: "
    + "Input should be 'buy_next_time', 'still_good' or 'plenty'; " * 6
    + ")"
)


@pytest.fixture(autouse=True)
def _no_background_slots_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with background's slots free, so one test's stuck work can't turn the
    next test's answer into the busy reply."""
    monkeypatch.setattr(background, "_in_flight", 0)


def _flip(item: str, to: str = "buy_next_time") -> dict[str, Any]:
    return {"kind": "pantry_flip", "args": {"item": item, "to": to}}


def _parsed(*intents: dict[str, Any], unclear: tuple[str, ...] = ()) -> dict[str, Any]:
    """An IntentReply as the JSON data Claude returns."""
    return {"intents": list(intents), "unclear": list(unclear)}


def _bot(opener: PantryOpener) -> tuple[AnyApp, RecordingRequest]:
    """The real app over `opener`, with its own LatestWrites, once meals.bot.intents exists."""
    if _MISSING is not None:
        pytest.fail(
            "meals.bot.intents is not built yet (PLAN.md Phase 4, bot PR 2b: IntentReply with "
            "intents and unclear, and parse(text, *, items) making one tool-free claude_runner "
            f"run): {_MISSING}",
            pytrace=False,
        )
    return build_bot(opener)


def _send(opener: PantryOpener, text: str, *, kind: str = "message") -> list[dict[str, Any]]:
    """Send `text` from Steve's chat to a new app; return the parameters of each message the bot
    sent."""
    app, request = _bot(opener)
    dispatch(app, text_update(app.bot, 1, text, kind=kind))
    return request.sent()


def _answer(sent: list[dict[str, Any]]) -> str:
    """The bot's answer, the last message it sent, after checking every message was plain text
    (PTB leaves a None parse_mode off the wire)."""
    assert sent, "the bot sent nothing"
    formatted = [params for params in sent if "parse_mode" in params]
    assert formatted == [], f"a reply was sent with a parse_mode: {formatted}"
    text: str = sent[-1]["text"]
    return text


def _line_about(answer: str, word: str) -> str:
    """The one line of `answer` that mentions `word`, casefolded."""
    lines = [line for line in answer.casefold().splitlines() if word in line]
    assert len(lines) == 1, f"{len(lines)} lines mention {word!r}: {answer!r}"
    return lines[0]


def _status(pantry: FakePantry, name: str) -> str:
    item = pantry.get_item(name)
    assert item is not None, name
    return item.status


def _parse_failure() -> ClaudeRunnerError:
    """What claude_runner raises when Claude's output fails validation: raw_output holds the
    output (RAW_MARKER), and the error is chained from the ValidationError, whose text quotes the
    rejected value (CAUSE_MARKER)."""
    try:
        PantryFlipArgs.model_validate({"item": "eggs", "to": CAUSE_MARKER})
    except ValidationError as cause:
        error = ClaudeRunnerError(REASON, raw_output=f'{{"intents": [{{"to": "{RAW_MARKER}"}}]}}')
        error.__cause__ = cause
        return error
    raise AssertionError("the marker validated as a pantry_flip target")


NOT_YET_INTENTS = [
    pytest.param({"kind": "pick", "args": {"numbers": [1]}}, id="pick"),
    pytest.param(
        {"kind": "swap", "args": {"slot": "veg", "remove": "broccoli", "add": "green beans"}},
        id="swap",
    ),
    pytest.param({"kind": "custody", "args": {"custody": "wed+sat_sun"}}, id="custody"),
    pytest.param({"kind": "add_item", "args": {"item": "tortillas"}}, id="add_item"),
    pytest.param({"kind": "find", "args": {"request": "a sheet-pan dinner"}}, id="find"),
    pytest.param({"kind": "save", "args": {"number": 2, "add_to_week": True}}, id="save"),
    pytest.param(
        {"kind": "rate", "args": {"target": "fajitas", "rater": "miles", "rating": 1}}, id="rate"
    ),
    pytest.param({"kind": "mode", "args": {"mode": "components"}}, id="mode"),
    pytest.param(_flip("rice", "still_good"), id="pantry_flip_still_good"),
    pytest.param(_flip("rice", "plenty"), id="pantry_flip_plenty"),
]


def test_out_of_eggs_flips_and_echoes(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-4: "out of eggs" from Steve, parsed as pantry_flip(eggs → buy_next_time): eggs read back
    # buy_next_time (they were `have`), and the answer's eggs line states that resulting state.
    patched_claude.queue(_parsed(_flip("eggs")))

    answer = _answer(_send(PantryOpener(fake_pantry), "out of eggs"))

    assert _status(fake_pantry, "eggs") == "buy_next_time", "eggs weren't written"
    assert ON_THE_LIST in _line_about(answer, "eggs"), answer


def test_echo_names_the_item_the_pantry_wrote(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-4 with the PR's design: the echo comes from what the pantry returned, not from Claude's
    # words. "evoo" is olive oil's alias, so the pantry writes olive oil, and the answer names it.
    patched_claude.queue(_parsed(_flip("evoo")))

    answer = _answer(_send(PantryOpener(fake_pantry), "out of evoo"))

    assert _status(fake_pantry, "olive oil") == "buy_next_time", "olive oil wasn't written"
    assert "olive oil" in answer.casefold(), f"the answer doesn't name olive oil: {answer!r}"


def test_parse_is_one_tool_free_run_with_the_intent_schema(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # The parse: exactly one claude_runner.run per message, with schema=IntentReply (the CLI checks
    # Claude's output against it) and web=False (contracts lane, #35: the runner's default is
    # web=True, which gives the parse WebSearch and WebFetch), and Steve's text in the prompt,
    # along with the pantry's item names and aliases (olive oil's alias is evoo; tahini is not in
    # Steve's text). Its timeout keeps both of the runner's attempts inside background's deadline
    # (the design sets PARSE_TIMEOUT_S = 120; the runner's default, 600, would not).
    text = "we are out of eggs, note 4c1"
    patched_claude.queue(_parsed(_flip("eggs")))

    _send(PantryOpener(fake_pantry), text)

    assert len(patched_claude.calls) == 1, f"{len(patched_claude.calls)} claude runs"
    call = patched_claude.calls[0]
    assert call.schema is IntentReply, f"the parse ran with schema {call.schema!r}"
    assert call.web is False, "the parse ran with web tools"
    assert text in call.prompt, "Steve's text isn't in the parse prompt"
    missing = [word for word in ("olive oil", "evoo", "tahini") if word not in call.prompt]
    assert missing == [], f"the parse prompt lacks the pantry's names or aliases: {missing}"
    assert call.timeout * claude_runner.MAX_ATTEMPTS < DEADLINE_S, f"timeout={call.timeout}"


def test_each_intent_is_echoed_separately(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-6: two pantry intents in one message, each written and echoed on its own line.
    patched_claude.queue(_parsed(_flip("rice"), _flip("tahini")))

    answer = _answer(_send(PantryOpener(fake_pantry), "out of rice and tahini"))

    assert (_status(fake_pantry, "rice"), _status(fake_pantry, "tahini")) == (
        "buy_next_time",
        "buy_next_time",
    )
    rice, tahini = _line_about(answer, "rice"), _line_about(answer, "tahini")
    assert rice != tahini, f"rice and tahini share a line: {answer!r}"
    assert ON_THE_LIST in rice and ON_THE_LIST in tahini, answer


def test_unknown_item_writes_nothing_and_asks(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-6: one of three intents names an item the pantry doesn't have. It writes nothing and is
    # asked back ("don't know"); the known items before and after it are still written.
    patched_claude.queue(_parsed(_flip("rice"), _flip("zucchini blossoms"), _flip("tahini")))
    before = {item.name: item for item in fake_pantry.list_items()}

    answer = _answer(_send(PantryOpener(fake_pantry), "out of rice, zucchini blossoms, tahini"))

    after = {item.name: item for item in fake_pantry.list_items()}
    assert after.keys() == before.keys(), "the unknown item was added to the pantry"
    changed = {name for name, item in after.items() if item != before[name]}
    assert changed == {"rice", "tahini"}, f"items written: {changed}"
    assert UNKNOWN in _line_about(answer, "zucchini blossoms"), answer


def test_pantry_error_on_one_item_keeps_the_other_echoes(
    patched_claude: FakeClaudeRunner, sample_pantry_items: tuple[PantryItem, ...]
) -> None:
    # CR-1 (the pre-PR code review), with PLAN.md Phase 4 (the bot repeats back what changed) and
    # AC-6: one message parses to rice then eggs, and the pantry raises SQLite's "database is
    # locked" for eggs only. Rice is written and its line says it is on the list; eggs read back
    # unchanged and get a line of their own that doesn't. The message is answered per intent, not
    # as one failure: background's generic error reply would drop rice's echo.
    pantry = LockedPantry(sample_pantry_items, locked="eggs")
    patched_claude.queue(_parsed(_flip("rice"), _flip("eggs")))
    eggs_before = pantry.get_item("eggs")

    answer = _answer(_send(PantryOpener(pantry), "out of rice and eggs"))

    assert _status(pantry, "rice") == "buy_next_time", "rice wasn't written"
    assert pantry.get_item("eggs") == eggs_before, "eggs changed though their write raised"
    assert not answer.startswith(background._ERROR_PREFIX), f"one failure reply: {answer!r}"
    rice, eggs = _line_about(answer, "rice"), _line_about(answer, "eggs")
    assert rice != eggs, f"rice and eggs share a line: {answer!r}"
    assert ON_THE_LIST in rice and ON_THE_LIST not in eggs, answer


@pytest.mark.parametrize(
    "fragment",
    ["if the eggs run out by friday, add them", "bought eggs and rice today"],
    ids=["conditional", "purchase_report"],
)
def test_unmapped_fragment_is_asked_back(
    fragment: str, patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-7: text Claude couldn't map to an intent (a conditional, or a purchase report) comes back
    # in `unclear`. The answer asks it back in one line that quotes it, and nothing is written.
    patched_claude.queue(_parsed(unclear=(fragment,)))
    before = fake_pantry.list_items()

    answer = _answer(_send(PantryOpener(fake_pantry), fragment))

    assert fake_pantry.list_items() == before, "an unmapped fragment wrote to the pantry"
    assert "\n" not in answer and fragment in answer.casefold(), answer


def test_intent_and_unmapped_fragment_are_answered_on_their_own_lines(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-6 with AC-7: one message holds an intent the bot applies and a fragment Claude couldn't
    # map. Eggs are written and echoed; the fragment is asked back on a line of its own, and
    # nothing else is written (rice is only mentioned inside the fragment).
    fragment = "if rice runs low by friday"
    patched_claude.queue(_parsed(_flip("eggs"), unclear=(fragment,)))
    before = {item.name: item for item in fake_pantry.list_items()}

    answer = _answer(_send(PantryOpener(fake_pantry), f"out of eggs, and {fragment}"))

    changed = {item.name for item in fake_pantry.list_items() if item != before[item.name]}
    assert changed == {"eggs"}, f"items written: {changed}"
    eggs, asked = _line_about(answer, "eggs"), _line_about(answer, fragment)
    assert eggs != asked, f"the fragment shares the eggs line: {answer!r}"
    assert ON_THE_LIST in eggs, answer


@pytest.mark.parametrize("intent", NOT_YET_INTENTS)
def test_unsupported_kind_writes_nothing(
    intent: dict[str, Any], patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-7: every intent kind bot PR 2b can't apply gets one "can't do that yet" line and writes
    # nothing: the eight non-pantry kinds, and a pantry_flip to still_good or plenty (their
    # confirm_stocked write needs the message's Detroit date, AC-5, bot PR 2c).
    patched_claude.queue(_parsed(intent))
    before = fake_pantry.list_items()

    answer = _answer(_send(PantryOpener(fake_pantry), "rice is still good"))

    assert fake_pantry.list_items() == before, "an unsupported intent wrote to the pantry"
    assert "\n" not in answer and NOT_YET in answer.casefold(), answer


def test_claude_failure_is_one_plain_line(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry, caplog: pytest.LogCaptureFixture
) -> None:
    # AC-11: the parse raises ClaudeRunnerError. The answer is one plain-text line, cut to
    # ERROR_MAX_CHARS, that shows neither the raw output nor the chained ValidationError's text.
    # The contracts lane's security review of #35: only `reason` is logged, so no meals record
    # holds either marker or carries a traceback (background's own logger.exception must never
    # see a parse failure), and the reason is logged.
    caplog.set_level(logging.DEBUG, logger="meals")
    patched_claude.queue(_parse_failure())
    before = fake_pantry.list_items()

    sent = _send(PantryOpener(fake_pantry), "out of eggs")

    answer = _answer(sent)
    assert "\n" not in answer and 0 < len(answer) <= ERROR_MAX_CHARS, answer
    shown = "\n".join(params["text"] for params in sent)
    assert RAW_MARKER not in shown and CAUSE_MARKER not in shown, shown
    records = [record for record in caplog.records if record.name.split(".")[0] == "meals"]
    logged = [record.getMessage() + (record.exc_text or "") for record in records]
    leaks = [text for text in logged if RAW_MARKER in text or CAUSE_MARKER in text]
    assert leaks == [], "a meals record shows Claude's output"
    assert [record.name for record in records if record.exc_info] == [], "a meals traceback"
    assert any(REASON_HEAD in text for text in logged), "the parse failure's reason isn't logged"
    assert fake_pantry.list_items() == before


@pytest.mark.parametrize("text", ["out of eggs", "/pantry"], ids=["free_text", "pantry_command"])
def test_edited_message_is_not_acted_on(
    text: str, patched_claude: FakeClaudeRunner, fake_pantry: FakePantry
) -> None:
    # AC-12: an edit arrives as a new update_id. It is never acted on: no Claude call, no pantry
    # opened (so an edited /pantry doesn't re-run /pantry), nothing written. The one reply is a
    # plain line asking Steve to send it again as a new message.
    opener = PantryOpener(fake_pantry)
    before = fake_pantry.list_items()

    sent = _send(opener, text, kind="edited_message")

    answer = _answer(sent)
    assert patched_claude.calls == [], "an edited message was parsed"
    assert opener.opened == 0, "an edited message opened the pantry"
    assert fake_pantry.list_items() == before
    assert len(sent) == 1 and "\n" not in answer and RESEND in answer.casefold(), sent


class WriteSignallingPantry(FakePantry):
    """A FakePantry that records each `flip_status` and sets `wrote` once one has run."""

    def __init__(self, items: tuple[PantryItem, ...]) -> None:
        super().__init__(items)
        self.wrote = threading.Event()
        self.flips: list[str] = []

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        item = super().flip_status(name, status)
        self.flips.append(name)
        self.wrote.set()
        return item


def test_older_update_finishing_last_never_overwrites(
    patched_claude: FakeClaudeRunner,
    sample_pantry_items: tuple[PantryItem, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC-8 end to end, with the team-lead's D5 ruling (newer = arrived later). Two messages reach
    # one app (so one LatestWrites) with update_ids running DOWN, 900 then 12, as after a quiet
    # week. Both parses run at once in background's workers; the first message's parse waits
    # (at most WAIT_S) until the second message has written, so the first finishes last. The
    # second is newer and wins: it is the only write. The first's late echo says a newer message
    # already set eggs. Ordering by update_id, a LatestWrites per message, or a ticket taken after
    # the parse each let the first message write eggs again.
    pantry = WriteSignallingPantry(sample_pantry_items)
    waited: list[bool] = []

    def parse_in_turn(prompt: str, *args: Any, **kwargs: Any) -> Any:
        if FIRST_TEXT in prompt:
            waited.append(pantry.wrote.wait(WAIT_S))
        return patched_claude.run(prompt, *args, **kwargs)

    monkeypatch.setattr(claude_runner, "run", parse_in_turn)
    patched_claude.queue(_parsed(_flip("eggs")), _parsed(_flip("eggs")))
    app, request = _bot(PantryOpener(pantry))

    dispatch(app, text_update(app.bot, 900, FIRST_TEXT), text_update(app.bot, 12, SECOND_TEXT))

    assert waited == [True], "the first message's parse never saw the second message's write"
    assert len(pantry.flips) == 1, f"both messages wrote: {pantry.flips}"
    assert _status(pantry, "eggs") == "buy_next_time", "the second message didn't write eggs"
    _answer(request.sent())  # every reply plain text
    lines = [
        line
        for params in request.sent()
        for line in params["text"].casefold().splitlines()
        if "eggs" in line
    ]
    assert len([line for line in lines if NEWER in line]) == 1, lines
    assert len([line for line in lines if ON_THE_LIST in line]) == 1, lines


def test_parse_finishing_past_the_deadline_writes_nothing(
    patched_claude: FakeClaudeRunner, fake_pantry: FakePantry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-9 end to end: the parse returns after background's deadline for this message has passed,
    # so nothing is written. The test patches time.monotonic process-wide (the design's "nothing
    # written once time.monotonic() > apply_by") and moves it DEADLINE_S + 1 s ahead while the
    # parse runs. asyncio's clock moves with it, and a bot that binds `from time import
    # monotonic` at import would not see the jump and would fail this test. A handler that sets
    # apply_by after the parse, or not from the message's start, writes eggs here.
    real_clock = time.monotonic
    late_by = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real_clock() + late_by[0])

    def slow_parse(*args: Any, **kwargs: Any) -> Any:
        late_by[0] = DEADLINE_S + 1.0
        return patched_claude.run(*args, **kwargs)

    monkeypatch.setattr(claude_runner, "run", slow_parse)
    patched_claude.queue(_parsed(_flip("eggs")))
    before = fake_pantry.list_items()

    _send(PantryOpener(fake_pantry), "out of eggs")

    assert len(patched_claude.calls) == 1, "the parse never ran"
    assert fake_pantry.list_items() == before, "the parse finished past the deadline and wrote"
