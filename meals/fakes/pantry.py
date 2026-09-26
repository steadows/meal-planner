import math
from collections.abc import Iterable
from datetime import date, timedelta

from meals.contracts import PantryItem, PantryStatus

STILL_GOOD_DAYS = 7


def _ask_date(item: PantryItem) -> date | None:
    """`next_ask_on`, else PLAN's 90% point: ceil(9 * interval / 10) days after the last purchase."""
    if item.next_ask_on is not None:
        return item.next_ask_on
    if item.typical_interval_days is None or item.last_purchased is None:
        return None
    ninety_percent = -(-9 * item.typical_interval_days // 10)  # ceil, in integers: no float
    return item.last_purchased + timedelta(days=ninety_percent)


def _is_due(item: PantryItem, on: date) -> bool:
    ask = _ask_date(item)
    return item.status == "buy_next_time" or (ask is not None and on >= ask)


def _due_order(item: PantryItem, on: date) -> tuple[bool, bool, int, str]:
    """Flagged first (no ask date last among them), then most days past the ask date, then name."""
    ask = _ask_date(item)
    days_past = 0 if ask is None else (on - ask).days
    return (item.status != "buy_next_time", ask is None, -days_past, item.name.casefold())


class FakePantry:
    """In-memory Pantry following the Protocol's rules. Interval learning is the real pantry's:
    the fake leaves `typical_interval_days` as given."""

    def __init__(self, items: Iterable[PantryItem] = ()) -> None:
        self._items: dict[int, PantryItem] = {item.id: item for item in items}
        self._purchases: frozenset[tuple[int, date]] = frozenset()

    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        due = (i for i in self._items.values() if i.category == "staple" and _is_due(i, on))
        return tuple(sorted(due, key=lambda item: _due_order(item, on)))

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        item = self._find(name)
        return None if item is None else self._replace(item, {"status": status})

    def confirm_stocked(self, name: str, on: date, plenty: bool = False) -> PantryItem | None:
        """Steve says an item is still stocked: status becomes `have`. "Still good" asks again a
        week after `on`; `plenty` asks again one interval after `on` (a week if there's no
        interval). Logs no purchase, and repeating it for the same `on` changes nothing. The real
        pantry may also lengthen the learned interval.

        Not on the `Pantry` Protocol yet: it joins in a one-line contracts PR once the real pantry
        implements it, so neither lane's PR has to merge first."""
        item = self._find(name)
        if item is None:
            return None
        interval = item.typical_interval_days
        wait = interval if plenty and interval is not None else STILL_GOOD_DAYS
        return self._replace(item, {"status": "have", "next_ask_on": on + timedelta(days=wait)})

    def log_purchase(
        self, name: str, on: date, qty: float | None = None, price_cents: int | None = None
    ) -> PantryItem | None:
        if qty is not None and not (math.isfinite(qty) and qty > 0):
            raise ValueError(f"qty must be a finite number above 0, got {qty}")
        if price_cents is not None and price_cents < 0:
            raise ValueError(f"price_cents can't be negative, got {price_cents}")
        item = self._find(name)
        if item is None or (item.id, on) in self._purchases:
            return item
        self._purchases = self._purchases | {(item.id, on)}
        if item.last_purchased is not None and on < item.last_purchased:
            return item
        return self._replace(item, {"last_purchased": on, "status": "have", "next_ask_on": None})

    def get_item(self, name: str) -> PantryItem | None:
        return self._find(name)

    def list_items(self) -> tuple[PantryItem, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: item.id))

    def _find(self, name: str) -> PantryItem | None:
        """Exact name first, so one item's alias can't shadow another item's name."""
        wanted = name.strip().casefold()
        items = self.list_items()
        by_name = (item for item in items if item.name.casefold() == wanted)
        by_alias = (item for item in items if wanted in {a.casefold() for a in item.aliases})
        return next(by_name, None) or next(by_alias, None)

    def _replace(self, item: PantryItem, changes: dict[str, object]) -> PantryItem:
        updated = item.model_copy(update=changes)
        self._items = self._items | {item.id: updated}
        return updated
