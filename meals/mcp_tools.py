"""The pantry's MCP tools: how Claude reads and updates the pantry (PLAN Phase 3).

`uv run python -m meals.mcp_tools` serves them over stdio. Register with
`claude mcp add --transport stdio pantry --scope project -- uv run python -m meals.mcp_tools`.

The arguments come from Claude reading Steve's free text, so this is a trust boundary:
- every expected failure becomes a `ToolError` whose text Claude can act on (any other exception
  reaches the client only as "Error executing tool <name>");
- dates are kept to a window around today, checked before the pantry is opened;
- no tool can write the product map, because the cart opens those URLs in Steve's logged-in
  Chrome session.

The server can't tell who is calling, so the rule on who may call lives where it is mounted: a
Claude run that has these tools must not also have web, Chrome or file tools (no untrusted input
next to pantry writes). `claude_runner`'s default tools are WebSearch and WebFetch, so the bot's
free-text run can't mount these on the defaults.

Stdout is the protocol, so nothing here prints. The pantry logs its writes and the SDK logs failed
calls, both on stderr.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from datetime import date, timedelta
from typing import Protocol

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from meals.contracts import Pantry, PantryItem, PantryStatus
from meals.db import get_db
from meals.pantry import SqlitePantry

# How far `on` may sit from today: a year back covers a late-logged purchase; one day ahead covers
# a clock that has just ticked past midnight. Anything else is a transcription slip.
PAST_DAYS = 366
FUTURE_DAYS = 1


class _PantryTools(Pantry, Protocol):
    """`Pantry` plus `confirm_stocked`, until contracts adds it to the Protocol."""

    def confirm_stocked(self, name: str, on: date, plenty: bool = False) -> PantryItem | None: ...


OpenPantry = Callable[[], AbstractContextManager[_PantryTools]]


@contextmanager
def open_real_pantry() -> Iterator[SqlitePantry]:
    """The real pantry over a fresh connection, closed on exit.

    One per tool call: the SDK runs sync tools in a worker thread, and a sqlite connection can't
    be shared across threads.
    """
    with closing(get_db()) as conn:
        yield SqlitePantry(conn)


def build_server(open_pantry: OpenPantry, today: Callable[[], date] = date.today) -> MCPServer:
    """The six pantry tools. `open_pantry` yields a pantry for one call; `today` is read per call."""
    server = MCPServer("pantry")

    @contextmanager
    def pantry() -> Iterator[_PantryTools]:
        with open_pantry() as opened:
            try:
                yield opened
            except ValueError as err:  # bad qty or price, or a PantryRowError naming the fix
                raise ToolError(str(err)) from err

    def day(on: date | None) -> date:
        now = today()
        earliest, latest = now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
        if on is None:
            return now
        if not earliest <= on <= latest:
            raise ToolError(
                f"{on.isoformat()} is out of range: use a date from {earliest.isoformat()} "
                f"to {latest.isoformat()}"
            )
        return on

    def found(item: PantryItem | None, name: str) -> PantryItem:
        if item is None:
            raise ToolError(f"no pantry item called {name!r}")
        return item

    @server.tool()
    def list_pantry() -> tuple[PantryItem, ...]:
        """Every pantry item with its status, category, last purchase and product details."""
        with pantry() as p:
            return p.list_items()

    @server.tool()
    def staples_due(on: date | None = None) -> tuple[PantryItem, ...]:
        """Staples to ask Steve about on `on` (default today): flagged buy-next-time first, then
        the most overdue."""
        checked = day(on)
        with pantry() as p:
            return p.staples_due(checked)

    @server.tool()
    def resolve_product(name: str) -> PantryItem:
        """The pantry item with this name or alias (case-insensitive), with its Meijer product."""
        with pantry() as p:
            return found(p.get_item(name), name)

    @server.tool()
    def set_status(name: str, status: PantryStatus) -> PantryItem:
        """Mark an item as `have` or `buy_next_time` (Steve ran out, or used the last of it)."""
        with pantry() as p:
            return found(p.flip_status(name, status), name)

    @server.tool()
    def log_purchase(
        name: str,
        on: date | None = None,
        qty: float | None = None,
        price_cents: int | None = None,
    ) -> PantryItem:
        """Record that Steve bought the item on `on` (default today). Repeating the same day is
        harmless."""
        checked = day(on)
        with pantry() as p:
            return found(p.log_purchase(name, checked, qty, price_cents), name)

    @server.tool()
    def confirm_stocked(name: str, plenty: bool = False, on: date | None = None) -> PantryItem:
        """Steve says the item is still good: ask again in a week, or after a full interval when
        `plenty` is true ("we have plenty")."""
        checked = day(on)
        with pantry() as p:
            return found(p.confirm_stocked(name, checked, plenty), name)

    return server


def main() -> None:
    build_server(open_real_pantry).run()


if __name__ == "__main__":
    main()
