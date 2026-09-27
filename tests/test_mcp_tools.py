"""meals.mcp_tools: the pantry's MCP tools, the LLM-callable boundary over Steve's pantry (PR 3).

Authority: `.context/seams/lane-b-pantry-pr3-mcp-tools.md` [Seams] (the tool table, the error
mapping, the date window, the security invariants), whose Revision 2 [Rev2] wins over the rest of
it (writes require `on`; strict argument types; only PantryRowError is translated; a missing
database is refused); the PR 3 RED brief [Brief] (`open_real_pantry()` is public;
`build_server(open_pantry, today=date.today)`), whose numbered requirements are cited [R1]..[R8];
and the `Pantry` Protocol docstrings in meals/contracts.py [Protocol]. Expected values are worked
out by hand from conftest's `sample_pantry_items`, never from the code. The server is driven
in-process through the SDK's own `Client`, over `FakePantry`; the real-pantry tests at the end use
a real database.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, closing, nullcontext
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, Tool

import meals.mcp_tools as mcp_tools
from meals.config import PROJECT_ROOT
from meals.contracts import PantryItem, PantryStatus
from meals.db import get_db
from meals.fakes import FakePantry
from meals.pantry import PantryRowError, SeedItem, SqlitePantry

# The injected clock. conftest's TODAY (2026-09-26) is the day these tests were written, so a tool
# reading the system clock instead of `today()` would pass on that day; this one is already past.
# On CLOCK the due staples are butter (flagged) and tahini (asked from 2026-07-22). Olive oil's ask
# date, 2026-09-24, is after it, so the system clock (2026-09-26 or later) also lists olive oil.
CLOCK = date(2026, 9, 23)
DAY = CLOCK.isoformat()  # CLOCK as the client sends it
# [Seams] "Date window": `on` must be within [today - 366 days, today + 1 day], inclusive.
EARLIEST, LATEST = CLOCK - timedelta(days=366), CLOCK + timedelta(days=1)  # 2025-09-22, 2026-09-24

# [Seams] the tool table: every tool and its arguments.
TOOL_ARGS = {
    "list_pantry": set(),
    "staples_due": {"on"},
    "resolve_product": {"name"},
    "set_status": {"name", "status"},
    "log_purchase": {"name", "on", "qty", "price_cents"},
    "confirm_stocked": {"name", "plenty", "on"},
}
# One valid call per tool, on an item the fixture holds. The writes take the message's date [Rev2].
CALLS = [
    ("list_pantry", {}),
    ("staples_due", {}),
    ("resolve_product", {"name": "butter"}),
    ("set_status", {"name": "butter", "status": "have"}),
    ("log_purchase", {"name": "butter", "on": DAY}),
    ("confirm_stocked", {"name": "butter", "on": DAY}),
]
CALL_IDS = [tool for tool, _ in CALLS]
# The tools taking `on`, each with its other arguments.
DATED = [(tool, args) for tool, args in CALLS if "on" in TOOL_ARGS[tool]]
DATED_IDS = [tool for tool, _ in DATED]

# An item with a product map, for resolve_product.
TORTILLAS = PantryItem(
    id=7,
    name="corn tortillas",
    aliases=("tortillas",),
    category="perishable",
    meijer_product_id="100007",
    meijer_url="https://www.meijer.com/shopping/product/example-corn-tortillas/100007.html",
    preferred_product_name="Example Corn Tortillas 30 ct",
)
ROW_ERROR = (
    "pantry_item 3 ('butter') is invalid; re-run the seed loader with a corrected row to fix it. "
    "Details: meijer_url must be an https URL on meijer.com"
)


@pytest.fixture
def by_name(sample_pantry_items: tuple[PantryItem, ...]) -> dict[str, PantryItem]:
    return {item.name: item for item in sample_pantry_items}


class _Opener:
    """`open_pantry` over one pantry, counting how often a call opened and closed it. A class, not
    a @contextmanager generator: a dropped generator runs its `finally` when it's garbage
    collected, which would count an exit that no `__exit__` call made."""

    def __init__(self, pantry: FakePantry) -> None:
        self.pantry = pantry
        self.entered = 0
        self.exited = 0

    def __call__(self) -> _Opener:
        return self

    def __enter__(self) -> FakePantry:
        self.entered += 1
        return self.pantry

    def __exit__(self, *exc_info: object) -> None:
        self.exited += 1


class _FailingPantry(FakePantry):
    """Every pantry method raises `error`: SqlitePantry's reads raise PantryRowError on a bad row."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def list_items(self) -> tuple[PantryItem, ...]:
        raise self.error

    def staples_due(self, on: date) -> tuple[PantryItem, ...]:
        raise self.error

    def get_item(self, name: str) -> PantryItem | None:
        raise self.error

    def flip_status(self, name: str, status: PantryStatus) -> PantryItem | None:
        raise self.error

    def log_purchase(
        self, name: str, on: date, qty: float | None = None, price_cents: int | None = None
    ) -> PantryItem | None:
        raise self.error

    def confirm_stocked(self, name: str, on: date, plenty: bool = False) -> PantryItem | None:
        raise self.error


class _CallFailed(Exception):
    pass


def _server(
    pantry: FakePantry, today: Callable[[], date] = lambda: CLOCK
) -> tuple[MCPServer, _Opener]:
    opener = _Opener(pantry)
    return mcp_tools.build_server(opener, today=today), opener


def _call(server: MCPServer, tool: str, args: dict[str, Any]) -> CallToolResult:
    async def call() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(tool, args)

    return anyio.run(call)


def _tools(server: MCPServer) -> list[Tool]:
    async def listing() -> list[Tool]:
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(listing)


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


def _ok(result: CallToolResult) -> Any:
    """The structured result of a call that must have succeeded."""
    assert not result.is_error, _text(result)
    return result.structured_content


def _dump(*items: PantryItem) -> dict[str, Any]:
    """A `tuple[PantryItem, ...]` result as the client receives it."""
    return {"result": [item.model_dump(mode="json") for item in items]}


def _after(items: tuple[PantryItem, ...], changed: PantryItem) -> tuple[PantryItem, ...]:
    """The pantry once `changed` is written over the item with its id, and nothing else is."""
    return tuple(changed if item.id == changed.id else item for item in items)


# ── the surface ([R1]) ─────────────────────────────────────────────────────────


def test_the_server_offers_exactly_the_six_tools_with_the_spec_arguments(
    fake_pantry: FakePantry,
) -> None:  # [R1]; [Seams] the tool table, "No tool writes the product map"
    # Exact equality is the witness that no tool can set meijer_url, meijer_product_id or
    # preferred_product_name, and that there is no save_product tool.
    tools = _tools(_server(fake_pantry)[0])
    assert {tool.name: set(tool.input_schema.get("properties", {})) for tool in tools} == TOOL_ARGS
    assert all(tool.description and tool.description.strip() for tool in tools)
    # [Rev2] "Writes take the message's date": `on` is required on the writes, optional on the read.
    required = {tool.name: set(tool.input_schema.get("required", ())) for tool in tools}
    assert "on" in required["log_purchase"] and "on" in required["confirm_stocked"]
    assert "on" not in required["staples_due"]


# ── one pantry per call ([R6]) ─────────────────────────────────────────────────


def test_building_the_server_and_listing_its_tools_never_opens_the_pantry(
    fake_pantry: FakePantry,
) -> None:  # [R6]; that no pantry data reaches a description is its own test, on a real database
    server, opener = _server(fake_pantry)
    _tools(server)
    assert (opener.entered, opener.exited) == (0, 0)


@pytest.mark.parametrize(("tool", "args"), CALLS, ids=CALL_IDS)
def test_each_call_opens_the_pantry_once_and_closes_it(
    fake_pantry: FakePantry, tool: str, args: dict[str, Any]
) -> None:  # [R6]; [Seams] "Opened per tool call ... then closed"
    server, opener = _server(fake_pantry)
    _ok(_call(server, tool, args))
    assert (opener.entered, opener.exited) == (1, 1)


# ── each tool maps to its pantry method ([R2]) ──────────────────────────────────


def test_list_pantry_returns_every_item_in_id_order(
    fake_pantry: FakePantry, sample_pantry_items: tuple[PantryItem, ...]
) -> None:  # [R2]; [Seams] list_pantry → list_items(); [Protocol] "Every item, in `id` order"
    assert _ok(_call(_server(fake_pantry)[0], "list_pantry", {})) == _dump(*sample_pantry_items)


@pytest.mark.parametrize(
    ("args", "due"),
    [
        ({"on": "2026-07-01"}, ("butter",)),  # tahini isn't asked about until 2026-07-22
        ({}, ("butter", "tahini")),  # on CLOCK
    ],
    ids=["on given", "on omitted: today()"],
)
def test_staples_due_asks_the_pantry_about_on_or_else_today(
    fake_pantry: FakePantry,
    by_name: dict[str, PantryItem],
    args: dict[str, Any],
    due: tuple[str, ...],
) -> (
    None
):  # [R2]; [Seams] staples_due → staples_due(on or today()); [Rev2] a read keeps the default
    result = _call(_server(fake_pantry)[0], "staples_due", args)
    assert _ok(result) == _dump(*(by_name[name] for name in due))


@pytest.mark.parametrize(
    "name",
    ["corn tortillas", "CORN Tortillas", "  Tortillas "],
    ids=["name", "name in another case", "alias, padded, in another case"],
)
def test_resolve_product_finds_an_item_by_name_or_alias_with_its_product_map(
    sample_pantry_items: tuple[PantryItem, ...], name: str
) -> None:  # [R2]; [Seams] resolve_product → get_item(name); [Protocol] strip().casefold()
    pantry = FakePantry((*sample_pantry_items, TORTILLAS))
    result = _call(_server(pantry)[0], "resolve_product", {"name": name})
    assert _ok(result) == TORTILLAS.model_dump(mode="json")


@pytest.mark.parametrize(("name", "status"), [("rice", "buy_next_time"), ("butter", "have")])
def test_set_status_flips_the_stored_item(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    by_name: dict[str, PantryItem],
    name: str,
    status: PantryStatus,
) -> None:  # [R2]; [Seams] set_status → flip_status
    result = _call(_server(fake_pantry)[0], "set_status", {"name": name, "status": status})
    expected = by_name[name].model_copy(update={"status": status})
    assert fake_pantry.list_items() == _after(sample_pantry_items, expected)  # the write landed
    assert _ok(result) == expected.model_dump(mode="json")


def test_log_purchase_restocks_the_stored_item(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    by_name: dict[str, PantryItem],
) -> None:  # [R2]; [Seams] log_purchase → log_purchase(name, on, …); [Protocol]
    # butter is flagged and was last bought 2026-09-21, so 2026-09-22 (not CLOCK) is its latest
    # purchase: it becomes last_purchased, status goes to `have` and next_ask_on is cleared. qty is
    # an int: [Rev2] "An int like 2 is still accepted".
    arguments = {"name": "butter", "on": "2026-09-22", "qty": 2, "price_cents": 499}
    result = _call(_server(fake_pantry)[0], "log_purchase", arguments)
    expected = by_name["butter"].model_copy(
        update={"last_purchased": date(2026, 9, 22), "status": "have", "next_ask_on": None}
    )
    assert fake_pantry.list_items() == _after(sample_pantry_items, expected)
    assert _ok(result) == expected.model_dump(mode="json")


@pytest.mark.parametrize(
    ("args", "ask"),
    [
        ({"on": "2026-09-01"}, date(2026, 9, 8)),  # still good: a week after `on`
        ({"on": "2026-09-01", "plenty": True}, date(2026, 10, 31)),  # plenty: 60 days after
    ],
    ids=["still good", "plenty"],
)
def test_confirm_stocked_pushes_the_stored_ask_date(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    by_name: dict[str, PantryItem],
    args: dict[str, Any],
    ask: date,
) -> None:  # [R2]; [Seams] confirm_stocked → confirm_stocked(name, on, plenty)
    # tahini: a 60-day interval and a current ask date of 2026-07-22, earlier than every push, so
    # the push alone decides next_ask_on (FakePantry.confirm_stocked's rules, worked by hand).
    result = _call(_server(fake_pantry)[0], "confirm_stocked", {"name": "tahini", **args})
    expected = by_name["tahini"].model_copy(update={"next_ask_on": ask})
    assert fake_pantry.list_items() == _after(sample_pantry_items, expected)
    assert _ok(result) == expected.model_dump(mode="json")


# ── the clock ([R3]) ───────────────────────────────────────────────────────────


def test_the_clock_is_read_on_every_call_not_when_the_server_is_built(
    fake_pantry: FakePantry, by_name: dict[str, PantryItem]
) -> None:  # [R3]; [Seams] "`today` is the injected clock"
    now = [date(2026, 7, 1)]
    server, _ = _server(fake_pantry, today=lambda: now[0])
    before = _call(server, "staples_due", {})
    now[0] = CLOCK
    after = _call(server, "staples_due", {})
    # The date window moves with it, on every tool taking `on`: as of 2026-07-01 the window ends
    # on 2026-07-02, so a clock read when the server was built would refuse all three calls.
    at_the_edge = _call(server, "staples_due", {"on": LATEST.isoformat()})
    _ok(_call(server, "log_purchase", {"name": "butter", "on": DAY}))
    _ok(_call(server, "confirm_stocked", {"name": "tahini", "on": DAY}))
    stored = {item.name: item for item in fake_pantry.list_items()}
    assert _ok(before) == _dump(by_name["butter"])
    assert _ok(after) == _dump(by_name["butter"], by_name["tahini"])
    _ok(at_the_edge)
    assert stored["butter"].last_purchased == CLOCK
    assert stored["tahini"].next_ask_on == date(2026, 9, 30)  # a week after CLOCK


# ── errors ([R4]) ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "args", "named"),
    [
        ("set_status", {"name": "rice", "status": "bogus"}, "bogus"),
        ("log_purchase", {"name": "rice", "on": "not-a-date"}, "not-a-date"),
        # [Rev2] "Writes take the message's date; there is no clock default"
        ("log_purchase", {"name": "rice"}, r"\bon\b"),
        ("confirm_stocked", {"name": "rice"}, r"\bon\b"),
        # [Rev2] "Argument types are strict and checked by the schema"
        ("log_purchase", {"name": "rice", "on": DAY, "price_cents": True}, "price_cents"),
        ("log_purchase", {"name": "rice", "on": DAY, "price_cents": "499"}, "price_cents"),
        ("log_purchase", {"name": "rice", "on": DAY, "price_cents": 4.0}, "price_cents"),
        ("log_purchase", {"name": "rice", "on": DAY, "qty": True}, "qty"),
        ("log_purchase", {"name": "rice", "on": DAY, "qty": "2"}, "qty"),
        ("confirm_stocked", {"name": "rice", "on": DAY, "plenty": "yes"}, "plenty"),
        # [Rev2] qty > 0 and price_cents >= 0 are the schema's now, so the pantry never sees them
        ("log_purchase", {"name": "rice", "on": DAY, "qty": 0}, "qty"),
        ("log_purchase", {"name": "rice", "on": DAY, "qty": -1.5}, "qty"),
        ("log_purchase", {"name": "rice", "on": DAY, "price_cents": -1}, "price_cents"),
    ],
    ids=[
        "unknown status",
        "malformed date",
        "log_purchase without on",
        "confirm_stocked without on",
        "price_cents true",
        "price_cents '499'",
        "price_cents 4.0",
        "qty true",
        "qty '2'",
        "plenty 'yes'",
        "qty 0",
        "qty < 0",
        "price_cents < 0",
    ],
)
def test_invalid_arguments_are_refused_before_the_pantry_opens(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    tool: str,
    args: dict[str, Any],
    named: str,
) -> None:  # [R2] invalid status; [R6] 0 enters; [Rev2]; [Seams] "validated by pydantic"
    server, opener = _server(fake_pantry)
    result = _call(server, tool, args)
    assert result.is_error
    # The rejected argument's own message, not "Unknown tool". A pattern, since "on" alone is in
    # every validation message ("validati-on").
    assert re.search(named, _text(result)), _text(result)
    assert (opener.entered, opener.exited) == (0, 0)
    assert fake_pantry.list_items() == sample_pantry_items


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("resolve_product", {"name": "saffron"}),
        ("set_status", {"name": "saffron", "status": "buy_next_time"}),
        ("log_purchase", {"name": "saffron", "on": DAY}),
        ("confirm_stocked", {"name": "saffron", "on": DAY}),
    ],
    ids=["resolve_product", "set_status", "log_purchase", "confirm_stocked"],
)
def test_an_unknown_name_is_a_tool_error_naming_it(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    tool: str,
    args: dict[str, Any],
) -> None:  # [R4]; [Seams] "Unknown name → ToolError(f"no pantry item called {name!r}")"
    server, opener = _server(fake_pantry)
    result = _call(server, tool, args)
    assert result.is_error
    assert "no pantry item called 'saffron'" in _text(result)
    assert fake_pantry.list_items() == sample_pantry_items
    assert (opener.entered, opener.exited) == (1, 1)  # [R6]: closed although the call failed


@pytest.mark.parametrize(("tool", "args"), CALLS, ids=CALL_IDS)
def test_a_corrupt_row_error_reaches_the_client_whichever_tool_hit_it(
    tool: str, args: dict[str, Any]
) -> None:  # [R4]; [Rev2] "The error mapping narrows to `PantryRowError` → ToolError"
    result = _call(_server(_FailingPantry(PantryRowError(ROW_ERROR)))[0], tool, args)
    assert result.is_error
    assert ROW_ERROR in _text(result)


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError, ValueError], ids=["sqlite error", "stray ValueError"]
)
@pytest.mark.parametrize(("tool", "args"), CALLS, ids=CALL_IDS)
def test_an_unexpected_error_reaches_the_client_opaque(
    tool: str, args: dict[str, Any], error: type[Exception]
) -> None:
    # [R4]; [Rev2] "every other exception, including a stray `ValueError`, stays opaque", which the
    # SDK renders as exactly "Error executing tool <name>": no internal detail, nothing re-dressed.
    server, opener = _server(_FailingPantry(error("secret path /x")))
    result = _call(server, tool, args)
    assert result.is_error
    assert _text(result) == f"Error executing tool {tool}"
    assert (opener.entered, opener.exited) == (1, 1)  # [R6]: closed although the call failed


# ── the date window ([R5]) ─────────────────────────────────────────────────────


@pytest.mark.parametrize("day", [EARLIEST, LATEST], ids=["today - 366 days", "today + 1 day"])
@pytest.mark.parametrize(("tool", "args"), DATED, ids=DATED_IDS)
def test_an_on_at_either_edge_of_the_window_reaches_the_pantry(
    fake_pantry: FakePantry, tool: str, args: dict[str, Any], day: date
) -> None:  # [R5]; [Seams] "Date window", inclusive at both ends ([Brief])
    server, opener = _server(fake_pantry)
    _ok(_call(server, tool, {**args, "on": day.isoformat()}))
    assert opener.entered == 1


@pytest.mark.parametrize(
    "day",
    [EARLIEST - timedelta(days=1), LATEST + timedelta(days=1), date(9999, 12, 31)],
    ids=["today - 367 days", "today + 2 days", "9999-12-31"],
)
@pytest.mark.parametrize(("tool", "args"), DATED, ids=DATED_IDS)
def test_an_on_outside_the_window_is_refused_before_the_pantry_opens(
    fake_pantry: FakePantry,
    sample_pantry_items: tuple[PantryItem, ...],
    tool: str,
    args: dict[str, Any],
    day: date,
) -> None:  # [R5]; [Seams] "ToolError naming the allowed range ... checked before any pantry call"
    server, opener = _server(fake_pantry)
    result = _call(server, tool, {**args, "on": day.isoformat()})
    assert result.is_error
    text = _text(result)
    assert EARLIEST.isoformat() in text and LATEST.isoformat() in text, text
    assert (opener.entered, opener.exited) == (0, 0)
    assert fake_pantry.list_items() == sample_pantry_items


# ── the real pantry ([R7] [R8]) ────────────────────────────────────────────────


@pytest.fixture
def pantry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_settings: None) -> Path:
    """A migrated pantry database holding butter and rice, which Settings.pantry_db points at."""
    path = tmp_path / "pantry.sqlite"
    monkeypatch.setenv("PANTRY_DB", str(path))
    seeds = (SeedItem(name="butter", category="staple"), SeedItem(name="rice", category="staple"))
    with closing(get_db()) as conn:
        SqlitePantry(conn).load_seed(seeds, CLOCK)
    return path


def test_no_pantry_data_reaches_a_tool_description_or_schema(pantry_db: Path) -> None:
    # [Seams] "Tool descriptions are static text written here; nothing from the DB is interpolated"
    marker = SeedItem(name="zanzibar marker", category="staple", aliases=("quokka alias",))
    with closing(get_db(pantry_db)) as conn:
        SqlitePantry(conn).load_seed((marker,), CLOCK)
    server = mcp_tools.build_server(mcp_tools.open_real_pantry, today=lambda: CLOCK)
    listing = " ".join(tool.model_dump_json() for tool in _tools(server)).casefold()
    assert "zanzibar" not in listing and "quokka" not in listing


def test_the_real_pantry_opens_on_the_calls_thread_and_the_writes_land(pantry_db: Path) -> None:
    # [R7]; [Seams] "Sync tools run in a worker thread", so the connection is opened per call
    server = mcp_tools.build_server(mcp_tools.open_real_pantry, today=lambda: CLOCK)
    _ok(_call(server, "set_status", {"name": "butter", "status": "buy_next_time"}))
    _ok(_call(server, "log_purchase", {"name": "rice", "on": DAY, "qty": 2, "price_cents": 499}))
    with closing(get_db(pantry_db)) as conn:  # a fresh connection: what was committed
        items = conn.execute("SELECT name, status, last_purchased FROM pantry_item ORDER BY id")
        stored = [tuple(row) for row in items]
        log = conn.execute(
            "SELECT i.name, l.purchased_on, l.qty, l.price_cents "
            "FROM purchase_log l JOIN pantry_item i ON i.id = l.item_id"
        )
        logged = [tuple(row) for row in log]
    assert stored == [("butter", "buy_next_time", None), ("rice", "have", DAY)]
    assert logged == [("rice", DAY, 2.0, 499)]  # qty and price_cents passed through


def test_a_purchase_retried_after_midnight_changes_nothing(pantry_db: Path) -> None:
    # [Rev2] "Writes take the message's date": a retried run carries the same `on`, so crossing
    # midnight makes it a same-day replay, which writes nothing ([Protocol] log_purchase).
    now = [CLOCK]
    server = mcp_tools.build_server(mcp_tools.open_real_pantry, today=lambda: now[0])
    bought = {"name": "rice", "on": DAY}
    _ok(_call(server, "log_purchase", bought))
    _ok(_call(server, "set_status", {"name": "rice", "status": "buy_next_time"}))
    now[0] = CLOCK + timedelta(days=1)  # midnight passes, and the run is retried
    _ok(_call(server, "log_purchase", bought))
    with closing(get_db(pantry_db)) as conn:
        log = conn.execute(
            "SELECT i.name, l.purchased_on FROM purchase_log l JOIN pantry_item i ON i.id = l.item_id"
        )
        logged = [tuple(row) for row in log]
        status = conn.execute("SELECT status FROM pantry_item WHERE name = 'rice'").fetchone()[0]
    assert logged == [("rice", DAY)]  # one purchase, not a second one dated a day later
    assert status == "buy_next_time"  # the flag set after the purchase survives the retry


def test_a_missing_database_is_refused_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> (
    None
):  # [Rev2] "`open_real_pantry` refuses a missing database ... never creates an empty pantry"
    path = tmp_path / "absent" / "pantry.sqlite"
    monkeypatch.setenv("PANTRY_DB", str(path))
    server = mcp_tools.build_server(mcp_tools.open_real_pantry, today=lambda: CLOCK)
    result = _call(server, "list_pantry", {})
    assert result.is_error
    text = _text(result)
    assert str(path) in text and re.search(r"seed[ _]loader", text, re.IGNORECASE), text
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("fails", [False, True], ids=["call returns", "call raises"])
def test_the_real_opener_closes_its_connection_when_the_call_ends(
    pantry_db: Path, fails: bool
) -> None:  # [R7]; [Brief] open_real_pantry "closes the connection on exit, including ... raises"
    # The body's exception must come out of the opener, not be swallowed by it.
    propagates: AbstractContextManager[object] = (
        pytest.raises(_CallFailed) if fails else nullcontext()
    )
    with propagates, mcp_tools.open_real_pantry() as pantry:
        assert [item.name for item in pantry.list_items()] == ["butter", "rice"]  # PANTRY_DB
        if fails:
            raise _CallFailed
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        pantry.list_items()


def test_python_dash_m_meals_mcp_tools_serves_the_tools_over_stdio(pantry_db: Path) -> None:
    # [R8]; [Seams] "a stdio server, `uv run python -m meals.mcp_tools`"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "meals.mcp_tools"],
        env={"PANTRY_DB": str(pantry_db)},
        cwd=PROJECT_ROOT,
    )

    async def session() -> tuple[set[str], CallToolResult, CallToolResult]:
        with anyio.fail_after(30):
            async with Client(server) as client:
                tools = (await client.list_tools()).tools
                listed = await client.call_tool("list_pantry", {})
                due = await client.call_tool("staples_due", {})  # main()'s default clock
                return {tool.name for tool in tools}, listed, due

    names, listed, due = anyio.run(session)
    assert names == set(TOOL_ARGS)
    assert [item["name"] for item in _ok(listed)["result"]] == ["butter", "rice"]
    assert _ok(due) == {"result": []}  # no interval and no purchase: never due, whatever the day
