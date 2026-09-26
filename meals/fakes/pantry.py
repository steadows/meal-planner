from collections.abc import Iterable
from datetime import date

from meals.contracts import PantryItem, PantryStatus

ASK_AT_FRACTION = 0.9


def _overdue_ratio(item: PantryItem, on: date) -> float:
    """Flagged items first, then the share of the interval that has passed (PLAN: ask at 90%)."""
    if item.status == "buy_next_time":
        return float("inf")
    interval = item.typical_interval_days
    if interval is None or interval <= 0 or item.last_purchased is None:
        return 0.0
    return (on - item.last_purchased).days / interval


class FakePantry:
    """In-memory pantry with PLAN's rough due rule. The real pantry lane owns interval learning."""

    def __init__(self, items: Iterable[PantryItem] = ()) -> None:
        self._items: dict[str, PantryItem] = {item.name: item for item in items}

    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        staples = (item for item in self._items.values() if item.category == "staple")
        ranked = sorted(
            ((item, _overdue_ratio(item, on)) for item in staples),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return tuple(item for item, ratio in ranked if ratio >= ASK_AT_FRACTION)

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        item = self._find(name)
        if item is None:
            return None
        flipped = item.model_copy(update={"status": status})
        self._items = self._items | {item.name: flipped}
        return flipped

    def _find(self, name: str) -> PantryItem | None:
        wanted = name.strip().lower()
        return next(
            (
                item
                for item in self._items.values()
                if wanted in {n.lower() for n in (item.name, *item.aliases)}
            ),
            None,
        )
