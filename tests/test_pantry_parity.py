"""FakePantry and SqlitePantry agree on the same replies (task B3).

Authority: `.context/seams/lane-b-pantry.md`, "Revision 4 — PR 2 (B3)", "Parity test", and
"Revision 5", which drops "still good" interval growth. Revision 4 makes the merged `FakePantry`
the reference for `confirm_stocked`. "Revision 6" (PR 2b) keeps it the reference (plenty waits at
least a week) and adds the rows for a short interval, a datetime `on`, and a stored name with
spaces around it. "Revision 7" (Steve's week floor) keeps it the reference for the new rule: the
push, or a later stored next_ask_on, is always stored, and the ask date is the later of next_ask_on
and the 90% point. This test is that revision's AC-7.

The two legitimately diverge in one place: a staple's second distinct purchase date, where the
real pantry re-learns its interval as the median gap and the fake keeps it. The script avoids that
rather than masking it. `insert_item` rows have no purchase-log history and the script buys each
item at most once, so neither pantry ever changes an interval, and every item is compared whole,
`typical_interval_days` included. With growth gone, the two `confirm_stocked`s are identical, so
the script may buy a staple after "still good" (Revision 5).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta, timezone

from meals.contracts import PantryItem
from meals.fakes import FakePantry
from meals.pantry import SqlitePantry

Insert = Callable[[PantryItem], None]
Step = Callable[[FakePantry | SqlitePantry, date], PantryItem | None]

# Two staples added to sample_pantry_items by the test, in both pantries, with no ask date: yeast,
# on a 5-day interval, and honey, stored with spaces around its name. The test numbers them on
# from the sample's highest id, so a sample item added to conftest can't collide with them (F9).
EXTRAS: tuple[dict[str, object], ...] = (
    {"name": "yeast", "category": "staple", "typical_interval_days": 5},
    {"name": " honey ", "category": "staple"},
)
# 23:30 at UTC-10 is already the next day in UTC and in the US Eastern zone (UTC-5, or UTC-4 in
# summer), so neither can stand in for it; [Protocol]: a datetime counts as its own day.
LATE_EVENING = time(23, 30, tzinfo=timezone(timedelta(hours=-10)))

# One reply per line, run against both pantries in order, on the `today` fixture's date, which
# sample_pantry_items is dated from. That day: olive oil's ask date (the 90% point) is 2 days past,
# rice's is 31 days out, tahini's 66 past, butter is flagged (ask date 14 days out), eggs is a
# perishable, chicken nuggets a fallback.
SCRIPT: tuple[tuple[str, Step], ...] = (
    ("still good, ask date passed", lambda p, day: p.confirm_stocked("olive oil", day)),
    ("still good, ask date later than a week", lambda p, day: p.confirm_stocked("rice", day)),
    ("plenty", lambda p, day: p.confirm_stocked("tahini", day, plenty=True)),
    ("plenty by alias, no interval", lambda p, day: p.confirm_stocked("Egg", day, plenty=True)),
    # max(5, 7): a week out, day 7.
    ("plenty, interval under a week", lambda p, day: p.confirm_stocked("yeast", day, plenty=True)),
    # Tomorrow at 23:30 counts as tomorrow: a week on (day 8) is later than day 7, so it's stored.
    (
        "still good on a datetime",
        lambda p, day: p.confirm_stocked(
            "yeast", datetime.combine(day + timedelta(days=1), LATE_EVENING)
        ),
    ),
    # No ask date: a week out, day 7.
    ("stored name with spaces, looked up plain", lambda p, day: p.confirm_stocked("honey", day)),
    ("flag a postponed staple", lambda p, day: p.flip_status("rice", "buy_next_time")),
    ("flag a fallback by alias", lambda p, day: p.flip_status("nuggets", "buy_next_time")),
    ("latest purchase clears next_ask_on", lambda p, day: p.log_purchase("tahini", day)),
    ("back-dated purchase", lambda p, day: p.log_purchase("butter", day - timedelta(days=30))),
    ("unknown name, confirm", lambda p, day: p.confirm_stocked("saffron", day)),
    ("unknown name, purchase", lambda p, day: p.log_purchase("saffron", day)),
)

# staples_due after the script, worked out by hand. Flagged: butter (ask date 14 days out), then
# rice (its 90% point, 31 out, later than the day 7 its "still good" stored). Then, most days past
# first: honey and olive oil from their next_ask_on (day 7, later than olive oil's 90% point, 2
# days past; the name breaks the tie, honey's being "honey"), yeast from its next_ask_on (day 8),
# tahini from its new purchase's 90% point (day 54).
DUE = {
    0: ["butter", "rice"],
    6: ["butter", "rice"],
    7: ["butter", "rice", " honey ", "olive oil"],
    8: ["butter", "rice", " honey ", "olive oil", "yeast"],
    53: ["butter", "rice", " honey ", "olive oil", "yeast"],
    54: ["butter", "rice", " honey ", "olive oil", "yeast", "tahini"],
}

# Then olive oil, postponed by "still good" above, is bought on day 7, the day it's asked about.
# Its unchanged 70-day interval puts the next ask at 90% (63 days) after that: day 70. (A grown
# interval, 72, would put it at day 72.) Day 69: honey is 62 days past, yeast 61, tahini 15.
THEN: tuple[tuple[str, Step], ...] = (
    (
        "bought after still good",
        lambda p, day: p.log_purchase("olive oil", day + timedelta(days=7)),
    ),
)
DUE_THEN = {
    69: ["butter", "rice", " honey ", "yeast", "tahini"],
    70: ["butter", "rice", " honey ", "yeast", "tahini", "olive oil"],
}


def _names(items: Iterable[PantryItem]) -> list[str]:
    return [item.name for item in items]


def test_the_fake_and_the_real_pantry_agree_on_the_same_replies(
    db: sqlite3.Connection,
    insert_item: Insert,
    sample_pantry_items: tuple[PantryItem, ...],
    today: date,
) -> None:
    last_id = max(item.id for item in sample_pantry_items)
    extras = tuple(
        PantryItem.model_validate({"id": last_id + n, **fields})
        for n, fields in enumerate(EXTRAS, start=1)
    )
    items = sample_pantry_items + extras
    fake = FakePantry(items)
    for item in items:
        insert_item(item)
    real = SqlitePantry(db)
    for script, due in ((SCRIPT, DUE), (THEN, DUE_THEN)):
        for label, step in script:
            assert step(real, today) == step(fake, today), label
        assert SqlitePantry(db).list_items() == fake.list_items()
        for days, names in due.items():
            day = today + timedelta(days=days)
            assert _names(real.staples_due(day)) == _names(fake.staples_due(day)) == names, day
