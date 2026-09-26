"""The pantry's MCP tools: how Claude reads and updates the pantry (PLAN Phase 3).

`uv run python -m meals.mcp_tools` serves them over stdio, on the pantry `PANTRY_DB` names. Mount
the server only in the Claude run that handles Steve's replies, through that run's own MCP config,
and never in the project `.mcp.json`, which also mounts chrome-devtools (see the rule below).

The arguments come from Claude reading Steve's free text, so this is a trust boundary:
- arguments are strictly typed and dates kept to a window around today, all checked before the
  pantry is opened (the SDK ignores argument names it doesn't know, so a misnamed optional
  argument falls back to its default);
- the writes take the date of Steve's message, never the clock, so a retried call repeats the
  same date and the pantry treats it as a replay;
- an unknown name or a corrupt row becomes a `ToolError` Claude can act on; anything else reaches
  the client only as "Error executing tool <name>", with the traceback logged here;
- no tool can write the product map, because the cart opens those URLs in Steve's logged-in
  Chrome session.

The server can't tell who is calling, so this module can't enforce who may call; the code that
starts the Claude run must. The rule: a run that has these tools must not also have web, Chrome or
file tools (no untrusted input next to pantry writes). `claude_runner`'s default tools are
WebSearch and WebFetch, so the bot's free-text run can't mount these on the defaults.

Stdout is the protocol, so nothing here prints. The pantry logs its writes and the SDK logs failed
calls, both on stderr.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from datetime import date, timedelta
from typing import Annotated, Protocol

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, StrictBool, StrictFloat, StrictInt

from meals.config import get_settings
from meals.contracts import Pantry, PantryItem, PantryStatus
from meals.db import get_db
from meals.pantry import PantryRowError, SqlitePantry

# How far `on` may sit from today: a year back covers a late-logged purchase; one day ahead covers
# a clock that has just ticked past midnight. Anything else is a transcription slip.
PAST_DAYS = 366
FUTURE_DAYS = 1

Qty = Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)]
PriceCents = Annotated[StrictInt, Field(ge=0)]


class _PantryTools(Pantry, Protocol):
    """`Pantry` plus `confirm_stocked`, until contracts adds it to the Protocol."""

    def confirm_stocked(self, name: str, on: date, plenty: bool = False) -> PantryItem | None: ...


OpenPantry = Callable[[], AbstractContextManager[_PantryTools]]


@contextmanager
def open_real_pantry() -> Iterator[SqlitePantry]:
    """The real pantry over a fresh connection, closed on exit.

    One per tool call: the SDK runs sync tools in a worker thread, and a sqlite connection can't
    be shared across threads. A missing database is refused rather than created empty, so a wrong
    `PANTRY_DB` can't pass for an empty pantry.
    """
    path = get_settings().pantry_db
    if not path.exists():
        raise ToolError(f"no pantry database at {path}: run the seed loader first")
    with closing(get_db(path)) as conn:
        yield SqlitePantry(conn)


@contextmanager
def _opened(open_pantry: OpenPantry) -> Iterator[_PantryTools]:
    """One pantry for one call. A PantryRowError names the bad row and its fix, so it reaches
    Claude; anything else stays opaque."""
    with open_pantry() as pantry:
        try:
            yield pantry
        except PantryRowError as err:
            raise ToolError(str(err)) from err


def _check_on(on: date, today: date) -> date:
    earliest, latest = today - timedelta(days=PAST_DAYS), today + timedelta(days=FUTURE_DAYS)
    if not earliest <= on <= latest:
        raise ToolError(
            f"{on.isoformat()} is out of range: use a date from {earliest.isoformat()} "
            f"to {latest.isoformat()}"
        )
    return on


def _found(item: PantryItem | None, name: str) -> PantryItem:
    if item is None:
        raise ToolError(f"no pantry item called {name!r}")
    return item


def build_server(open_pantry: OpenPantry, today: Callable[[], date] = date.today) -> MCPServer:
    """The six pantry tools. `open_pantry` yields a pantry for one call; `today` is read per call."""
    server = MCPServer("pantry")

    @server.tool()
    def list_pantry() -> tuple[PantryItem, ...]:
        """Every pantry item with its status, category, last purchase and product details."""
        with _opened(open_pantry) as pantry:
            return pantry.list_items()

    @server.tool()
    def staples_due(on: date | None = None) -> tuple[PantryItem, ...]:
        """Staples to ask Steve about on `on` (default today), most urgent first: flagged
        buy-next-time, then the most overdue. Ask about three at most."""
        day = today() if on is None else _check_on(on, today())
        with _opened(open_pantry) as pantry:
            return pantry.staples_due(day)

    @server.tool()
    def resolve_product(name: str) -> PantryItem:
        """The pantry item with this name or alias (case-insensitive), with its Meijer product."""
        with _opened(open_pantry) as pantry:
            return _found(pantry.get_item(name), name)

    @server.tool()
    def set_status(name: str, status: PantryStatus) -> PantryItem:
        """Set `buy_next_time` when Steve has run out or used the last of an item. `have` only
        undoes that: it records no purchase and doesn't move the next question, so use
        log_purchase for "bought it" and confirm_stocked for "still have it"."""
        with _opened(open_pantry) as pantry:
            return _found(pantry.flip_status(name, status), name)

    @server.tool()
    def log_purchase(
        name: str, on: date, qty: Qty | None = None, price_cents: PriceCents | None = None
    ) -> PantryItem:
        """Record that Steve bought the item. `on` is the date of his message, not today's date,
        so a retried call repeats it. One purchase per item per day: calling again for the same
        day changes nothing, so it can't correct qty or price."""
        day = _check_on(on, today())
        with _opened(open_pantry) as pantry:
            return _found(pantry.log_purchase(name, day, qty, price_cents), name)

    @server.tool()
    def confirm_stocked(name: str, on: date, plenty: StrictBool = False) -> PantryItem:
        """Steve says the item is still good: don't ask again for a week, or for a full interval
        when `plenty` is true ("we have plenty"). `on` is the date of his message."""
        day = _check_on(on, today())
        with _opened(open_pantry) as pantry:
            return _found(pantry.confirm_stocked(name, day, plenty), name)

    return server


def main() -> None:
    build_server(open_real_pantry).run()


if __name__ == "__main__":
    main()
