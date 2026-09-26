"""FakePantry and SqlitePantry agree on the same replies (task B3).

Authority: `.context/seams/lane-b-pantry.md`, "Revision 4 — PR 2 (B3)", "Parity test", and
"Revision 5", which drops "still good" interval growth. Revision 4 makes the merged `FakePantry`
the reference for `confirm_stocked`.

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
from datetime import date, timedelta

from meals.contracts import PantryItem
from meals.fakes import FakePantry
from meals.pantry import SqlitePantry

Insert = Callable[[PantryItem], None]
Step = Callable[[FakePantry | SqlitePantry, date], PantryItem | None]

# One reply per line, run against both pantries in order, on the `today` fixture's date, which
# sample_pantry_items is dated from. That day: olive oil's ask date (the 90% point) is 2 days past,
# rice's is 31 days out, tahini's 66 past, butter is flagged (ask date 14 days out), eggs is a
# perishable, chicken nuggets a fallback.
SCRIPT: tuple[tuple[str, Step], ...] = (
    ("still good, ask date passed", lambda p, day: p.confirm_stocked("olive oil", day)),
    ("still good, ask date later than a week", lambda p, day: p.confirm_stocked("rice", day)),
    ("plenty", lambda p, day: p.confirm_stocked("tahini", day, plenty=True)),
    ("plenty by alias, no interval", lambda p, day: p.confirm_stocked("Egg", day, plenty=True)),
    ("flag a postponed staple", lambda p, day: p.flip_status("rice", "buy_next_time")),
    ("flag a fallback by alias", lambda p, day: p.flip_status("nuggets", "buy_next_time")),
    ("latest purchase clears next_ask_on", lambda p, day: p.log_purchase("tahini", day)),
    ("back-dated purchase", lambda p, day: p.log_purchase("butter", day - timedelta(days=30))),
    ("unknown name, confirm", lambda p, day: p.confirm_stocked("saffron", day)),
    ("unknown name, purchase", lambda p, day: p.log_purchase("saffron", day)),
)

# staples_due after the script, worked out by hand. Flagged: butter (ask date 14 days out), then
# rice (next_ask_on 31 out). Then olive oil from its next_ask_on (day 7), tahini from its new
# purchase's 90% point (day 54).
DUE = {
    0: ["butter", "rice"],
    6: ["butter", "rice"],
    7: ["butter", "rice", "olive oil"],
    53: ["butter", "rice", "olive oil"],
    54: ["butter", "rice", "olive oil", "tahini"],
}

# Then olive oil, postponed by "still good" above, is bought on day 7, the day it's asked about.
# Its unchanged 70-day interval puts the next ask at 90% (63 days) after that: day 70. (A grown
# interval, 72, would put it at day 72.)
THEN: tuple[tuple[str, Step], ...] = (
    (
        "bought after still good",
        lambda p, day: p.log_purchase("olive oil", day + timedelta(days=7)),
    ),
)
DUE_THEN = {
    69: ["butter", "rice", "tahini"],
    70: ["butter", "rice", "tahini", "olive oil"],
}


def _names(items: Iterable[PantryItem]) -> list[str]:
    return [item.name for item in items]


def test_the_fake_and_the_real_pantry_agree_on_the_same_replies(
    db: sqlite3.Connection,
    insert_item: Insert,
    sample_pantry_items: tuple[PantryItem, ...],
    today: date,
) -> None:
    fake = FakePantry(sample_pantry_items)
    for item in sample_pantry_items:
        insert_item(item)
    real = SqlitePantry(db)
    for script, due in ((SCRIPT, DUE), (THEN, DUE_THEN)):
        for label, step in script:
            assert step(real, today) == step(fake, today), label
        assert SqlitePantry(db).list_items() == fake.list_items()
        for days, names in due.items():
            day = today + timedelta(days=days)
            assert _names(real.staples_due(day)) == _names(fake.staples_due(day)) == names, day
