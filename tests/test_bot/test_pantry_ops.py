"""meals.bot.pantry_ops.apply: how the bot writes parsed pantry intents (bot PR 2b).

Authority: PLAN.md, Phase 4 (on-the-go pantry updates from Steve's chat) and Runtime concurrency
(two background workers can finish out of order); ADR-0001, Consequences (background gives up on
a worker thread at its deadline but can't stop it); and the bot PR's Acceptance → proof table:
AC-8 (two messages about the same item whose parses finish out of order: the older one, finishing
last, does not overwrite the newer one's write, and its reply says a newer message already set the
item) and AC-9 (parse work that finishes after background's deadline writes nothing).

The seam, per the team-lead's D5 rulings: `apply(intents, *, pantry, ticket, apply_by, latest)
-> tuple[str, ...]`, one echo line per intent. "Newer" means arrived later, not a higher
update_id: Telegram picks ids at random after a quiet week. `latest` is a `LatestWrites()`, one per
Application, with no module-level state: `latest.ticket()` hands out increasing ints, and it keeps,
per item name (casefolded), the ticket of the newest write, under its own lock. apply writes nothing
once `time.monotonic()` is past `apply_by`. In bot PR 2b only a `pantry_flip` to `buy_next_time`
writes (`Pantry.flip_status`). Each test builds its own LatestWrites, so no state is shared.

Left to code review, not to a test here: the lock must cover the newest-ticket check, the pantry
write and recording the ticket together; and the free-text handler must take the ticket on the
event loop when the message arrives, before `background.start`, not in the worker thread. These
tests call apply one at a time, so they can't tell either difference.

The pre-PR code review's CR-1 adds: a pantry error on one intent fails only that intent. The
later intents still run, and the failed write is not recorded as the item's newest write.

Pantries are `meals.fakes.FakePantry` over the conftest items (olive oil, rice, butter
[buy_next_time], tahini, eggs, chicken nuggets), read back after each call; CR-1's is conftest's
LockedPantry over the same items.
"""

import sqlite3
import time

import pytest

from meals.contracts import PantryFlipArgs, PantryFlipIntent, PantryItem
from meals.fakes import FakePantry
from test_bot.conftest import LockedPantry

_MISSING: ImportError | None = None
try:
    from meals.bot import pantry_ops
except ImportError as exc:  # RED: bot PR 2b's pantry_ops; `_require_pantry_ops` names it
    _MISSING = exc

TIME_LEFT_S = 60.0  # an apply_by this far ahead hasn't passed when apply runs


def _require_pantry_ops() -> None:
    if _MISSING is not None:
        pytest.fail(
            "meals.bot.pantry_ops is not built yet (PLAN.md Phase 4, bot PR 2b, AC-8 and AC-9: "
            "LatestWrites() with ticket() -> int, and apply(intents, *, pantry, ticket, "
            f"apply_by, latest) -> tuple[str, ...]): {_MISSING}",
            pytrace=False,
        )


def _out_of(item: str) -> PantryFlipIntent:
    return PantryFlipIntent(kind="pantry_flip", args=PantryFlipArgs(item=item, to="buy_next_time"))


def _status(pantry: FakePantry, name: str) -> str:
    item = pantry.get_item(name)
    assert item is not None, name
    return item.status


def test_older_update_never_overwrites_newer(fake_pantry: FakePantry) -> None:
    # AC-8, two updates against the same LatestWrites, tickets taken in arrival order. The newer
    # update ("Eggs") writes first; Steve then buys eggs, so they read back `have`; then the older
    # update ("eggs", the same item once casefolded) finishes last. It must not write: eggs stay
    # `have`, and its echo says a newer message already set eggs. Two controls: the same older
    # update still writes rice (the memory is per item, not one newest ticket for everything), and
    # a still newer update after it writes eggs (a write doesn't lock the item).
    _require_pantry_ops()
    latest = pantry_ops.LatestWrites()
    older, newer, newest = latest.ticket(), latest.ticket(), latest.ticket()
    apply_by = time.monotonic() + TIME_LEFT_S
    pantry_ops.apply(
        (_out_of("Eggs"),), pantry=fake_pantry, ticket=newer, apply_by=apply_by, latest=latest
    )
    assert _status(fake_pantry, "eggs") == "buy_next_time", "the newer update didn't write eggs"
    assert fake_pantry.flip_status("eggs", "have") is not None  # bought meanwhile

    lines = pantry_ops.apply(
        (_out_of("eggs"), _out_of("rice")),
        pantry=fake_pantry,
        ticket=older,
        apply_by=apply_by,
        latest=latest,
    )

    assert _status(fake_pantry, "eggs") == "have", "the older update overwrote the newer write"
    assert len(lines) == 2, f"one echo line per intent: {lines}"
    eggs_lines = [line.casefold() for line in lines if "eggs" in line.casefold()]
    assert len(eggs_lines) == 1 and "newer message" in eggs_lines[0], lines
    assert _status(fake_pantry, "rice") == "buy_next_time", "the older update's rice was refused"

    assert fake_pantry.flip_status("eggs", "have") is not None
    pantry_ops.apply(
        (_out_of("eggs"),), pantry=fake_pantry, ticket=newest, apply_by=apply_by, latest=latest
    )
    assert _status(fake_pantry, "eggs") == "buy_next_time", "a newer update was refused"


def test_latest_writes_are_kept_per_instance(fake_pantry: FakePantry) -> None:
    # The team-lead's D5 ruling: no module-level state; each LatestWrites keeps its own. Another
    # app's LatestWrites hands out its ticket first; then this app's second ticket writes eggs.
    # Applied through that other LatestWrites, its earlier ticket has no write on record there,
    # so it writes. A map shared by every instance (with a shared counter or not) would refuse it,
    # and leak state between apps and between tests.
    _require_pantry_ops()
    other, mine = pantry_ops.LatestWrites(), pantry_ops.LatestWrites()
    other_ticket = other.ticket()
    mine.ticket()
    apply_by = time.monotonic() + TIME_LEFT_S
    pantry_ops.apply(
        (_out_of("eggs"),), pantry=fake_pantry, ticket=mine.ticket(), apply_by=apply_by, latest=mine
    )
    assert fake_pantry.flip_status("eggs", "have") is not None

    pantry_ops.apply(
        (_out_of("eggs"),), pantry=fake_pantry, ticket=other_ticket, apply_by=apply_by, latest=other
    )

    assert _status(fake_pantry, "eggs") == "buy_next_time", "two LatestWrites share their memory"


def test_no_write_after_deadline(fake_pantry: FakePantry) -> None:
    # AC-9: once apply_by has passed, apply writes nothing, for any of its intents (background
    # has already given up on this work and told Steve so). The control: the same intents with
    # time left write both items.
    _require_pantry_ops()
    latest = pantry_ops.LatestWrites()
    before = fake_pantry.list_items()

    pantry_ops.apply(
        (_out_of("eggs"), _out_of("rice")),
        pantry=fake_pantry,
        ticket=latest.ticket(),
        apply_by=time.monotonic() - 1.0,
        latest=latest,
    )

    assert fake_pantry.list_items() == before, "apply wrote after apply_by had passed"

    pantry_ops.apply(
        (_out_of("eggs"), _out_of("rice")),
        pantry=fake_pantry,
        ticket=latest.ticket(),
        apply_by=time.monotonic() + TIME_LEFT_S,
        latest=latest,
    )

    assert (_status(fake_pantry, "eggs"), _status(fake_pantry, "rice")) == (
        "buy_next_time",
        "buy_next_time",
    ), "apply wrote nothing with time left"


def test_pantry_error_is_not_recorded_as_a_write(
    sample_pantry_items: tuple[PantryItem, ...],
) -> None:
    # CR-1 (the pre-PR code review): the pantry raises SQLite's "database is locked" for eggs, the
    # first of two intents. apply still answers one line per intent and still writes rice after
    # it; eggs read back unchanged. The failed write is not recorded as eggs' newest write: once
    # the lock clears, a message that arrived earlier (a lower ticket) still writes eggs. A
    # LatestWrites that records the ticket before the write, or whatever the write did, refuses it
    # as "a newer message already set eggs".
    _require_pantry_ops()
    pantry = LockedPantry(sample_pantry_items, locked="eggs")
    latest = pantry_ops.LatestWrites()
    older, newer = latest.ticket(), latest.ticket()
    apply_by = time.monotonic() + TIME_LEFT_S
    eggs_before = pantry.get_item("eggs")

    try:
        lines = pantry_ops.apply(
            (_out_of("eggs"), _out_of("rice")),
            pantry=pantry,
            ticket=newer,
            apply_by=apply_by,
            latest=latest,
        )
    except sqlite3.Error as exc:
        pytest.fail(f"apply let eggs' {exc!r} escape, so rice never ran", pytrace=False)

    assert len(lines) == 2, f"one echo line per intent: {lines}"
    assert pantry.get_item("eggs") == eggs_before, "eggs changed though their write raised"
    assert _status(pantry, "rice") == "buy_next_time", "the intent after the failed one didn't run"

    pantry.locked = None
    pantry_ops.apply(
        (_out_of("eggs"),), pantry=pantry, ticket=older, apply_by=apply_by, latest=latest
    )

    assert _status(pantry, "eggs") == "buy_next_time", "the failed write blocked an older ticket"
