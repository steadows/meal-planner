"""meals.seed_loader: the seed CSV reader and its CLI (task B2).

Authority: the B2 dispatch brief (its numbered requirements are cited as [R18]..[R24]),
`.context/seams/lane-b-pantry.md` (`read_seed_csv`, CLI; the CSV format; Flags: seed URLs) and
PLAN.md, Concurrency lanes (Lane B done-when: "`staples_due()` right on seed data").
tests/fixtures/seed_pantry.csv is invented data; the expected items below are transcribed from it
by hand.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from meals.db import get_db

# RED until B2 lands: each test fails with this message instead of one collection error.
try:
    from meals.seed_loader import SeedError, main, read_seed_csv

    from meals.pantry import SeedItem, SqlitePantry
except ImportError as exc:
    _MISSING: str | None = (
        "meals.seed_loader must export read_seed_csv, main and SeedError, and meals.pantry "
        f"SqlitePantry and SeedItem; see .context/seams/lane-b-pantry.md. ({exc})"
    )
else:
    _MISSING = None

FIXTURE = Path(__file__).parent / "fixtures" / "seed_pantry.csv"
FIXTURE_NAMES = (
    "olive oil",
    "rice",
    "butter",
    "tahini",
    "couscous",
    "siete taco shells",
    "eggs",
    "chicken nuggets",
)
COLUMNS = (
    "name",
    "category",
    "aliases",
    "interval_days",
    "last_purchased",
    "default_qty",
    "default_unit",
    "meijer_product_id",
    "meijer_url",
    "preferred_product_name",
    "substitute_ok",
    "for_miles",
    "notes",
)
ON = date(2026, 9, 26)
PRODUCT = "https://www.meijer.com/shopping/product"


@pytest.fixture(autouse=True)
def _b2_seams_exist() -> None:
    if _MISSING is not None:
        pytest.fail(_MISSING)


def _seed(name: str, category: str = "staple", **fields: object) -> SeedItem:
    return SeedItem.model_validate({"name": name, "category": category, **fields})


def _csv(
    tmp_path: Path,
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str] = COLUMNS,
    encoding: str = "utf-8",
) -> Path:
    """A seed CSV with a header row; cells a row leaves out are blank."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = tmp_path / "seed.csv"
    path.write_text(buffer.getvalue(), encoding=encoding)
    return path


def _row_numbers(problems: Sequence[str]) -> set[int]:
    """The rows the problems name, written "row N" as in the brief's `SeedError` example."""
    found = [re.findall(r"\brow (\d+)\b", problem, re.IGNORECASE) for problem in problems]
    assert all(found), f"every problem names its row: {problems!r}"
    return {int(numbers[0]) for numbers in found}


def _dump(path: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    with closing(sqlite3.connect(path)) as conn:
        items = conn.execute("SELECT * FROM pantry_item ORDER BY id").fetchall()
        return items, conn.execute("SELECT * FROM purchase_log ORDER BY id").fetchall()


def _cli_db(tmp_path: Path, name: str) -> Path:
    """A migrated database holding one item, so "writes nothing" is a comparison, not a guess."""
    path = tmp_path / "cli.sqlite"
    with closing(get_db(path)) as conn, conn:
        conn.execute("INSERT INTO pantry_item (name, category) VALUES (?, 'staple')", (name,))
    return path


# ── read_seed_csv: the fixture ───────────────────────────────────────────────


def test_the_fixture_reads_into_seed_items_in_file_order() -> None:  # [R19] [R20]
    assert read_seed_csv(FIXTURE) == (
        _seed(
            "olive oil",
            aliases=("evoo", "extra virgin olive oil"),
            typical_interval_days=70,
            last_purchased=date(2026, 7, 11),
            default_qty=1,
            default_unit="bottle",
            meijer_product_id="100001",
            meijer_url=f"{PRODUCT}/example-olive-oil/100001.html",
            preferred_product_name="Example Olive Oil 16.9 oz",
        ),
        _seed(
            "rice",
            typical_interval_days=56,
            last_purchased=date(2026, 9, 1),
            default_qty=2,
            default_unit="lb",
            meijer_product_id="100002",
            meijer_url=f"{PRODUCT}/example-rice/100002.html",
            preferred_product_name="Example Long Grain Rice 2 lb",
        ),
        _seed(
            "butter",
            typical_interval_days=21,
            last_purchased=date(2026, 9, 7),
            default_qty=1,
            default_unit="lb",
            meijer_product_id="100003",
            meijer_url=f"{PRODUCT}/example-butter/100003.html",
            preferred_product_name="Example Salted Butter 1 lb",
        ),
        _seed(
            "tahini",
            aliases=("sesame paste",),
            typical_interval_days=60,
            last_purchased=date(2026, 7, 28),
            default_qty=1,
            default_unit="jar",
            meijer_product_id="100004",
            meijer_url=f"{PRODUCT}/example-tahini/100004.html",
            preferred_product_name="Example Tahini 16 oz",
            substitute_ok=False,
            notes="stir first, it separates",
        ),
        _seed(
            "couscous",
            typical_interval_days=60,
            default_qty=1,
            default_unit="box",
            notes="already in the house",
        ),
        _seed(
            "siete taco shells",
            aliases=("taco shells",),
            typical_interval_days=42,
            last_purchased=date(2026, 8, 26),
            default_qty=1,
            default_unit="box",
            meijer_product_id="100006",
            meijer_url=f"{PRODUCT}/example-taco-shells/100006.html",
            preferred_product_name="Example Grain Free Taco Shells",
            substitute_ok=False,
            for_miles=True,
        ),
        _seed(
            "eggs",
            "perishable",
            aliases=("egg",),
            last_purchased=date(2026, 9, 20),
            default_qty=1,
            default_unit="dozen",
            meijer_product_id="100007",
            meijer_url=f"{PRODUCT}/example-eggs/100007.html",
            preferred_product_name="Example Large Eggs 12 ct",
        ),
        _seed(
            "chicken nuggets",
            "fallback",
            aliases=("nuggets",),
            typical_interval_days=14,
            last_purchased=date(2026, 8, 1),
            default_qty=1,
            default_unit="bag",
            meijer_product_id="100008",
            meijer_url=f"{PRODUCT}/example-nuggets/100008.html",
            preferred_product_name="Example Chicken Nuggets",
            for_miles=True,
            notes="freezer lane",
        ),
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"], ids=["no BOM", "Excel BOM"])
def test_a_utf8_file_reads_the_same_with_or_without_a_bom(tmp_path: Path, encoding: str) -> None:
    # [R18]
    path = tmp_path / "seed.csv"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding=encoding)
    assert path.read_bytes().startswith(codecs.BOM_UTF8) is (encoding == "utf-8-sig")
    assert read_seed_csv(path) == read_seed_csv(FIXTURE)


# ── read_seed_csv: header and cells ──────────────────────────────────────────


@pytest.mark.parametrize(
    "columns",
    [
        ("category", "aliases"),
        ("name", "aliases"),
        ("name", "category", "colour"),
        ("name", "category", "interval"),
    ],
    ids=["no name", "no category", "unknown column", "near miss of interval_days"],
)
def test_a_header_missing_name_or_category_or_naming_an_unknown_column_is_rejected(
    tmp_path: Path, columns: tuple[str, ...]
) -> None:  # [R19]
    cells = {
        "name": "rice",
        "category": "staple",
        "aliases": "",
        "colour": "white",
        "interval": "56",
    }
    path = _csv(tmp_path, [{column: cells[column] for column in columns}], columns)
    with pytest.raises(SeedError) as excinfo:
        read_seed_csv(path)
    assert excinfo.value.problems


@pytest.mark.parametrize("columns", [("name", "category"), COLUMNS], ids=["two columns", "all"])
def test_only_name_and_category_are_needed_and_blank_cells_take_the_defaults(
    tmp_path: Path, columns: tuple[str, ...]
) -> None:  # [R19] [R20]
    path = _csv(tmp_path, [{"name": "rice", "category": "staple"}], columns)
    assert read_seed_csv(path) == (_seed("rice"),)


def test_cells_are_stripped(tmp_path: Path) -> None:  # [R20]
    path = _csv(
        tmp_path,
        [
            {
                "name": "  rice ",
                "category": " staple ",
                "aliases": " white rice ",
                "interval_days": " 56 ",
                "last_purchased": " 2026-09-01 ",
                "default_qty": " 2 ",
                "default_unit": "   ",
                "meijer_url": f" {PRODUCT}/example-rice/100002.html ",
                "substitute_ok": " no ",
                "notes": "  the big bag  ",
            }
        ],
    )
    assert read_seed_csv(path) == (
        _seed(
            "rice",
            aliases=("white rice",),
            typical_interval_days=56,
            last_purchased=date(2026, 9, 1),
            default_qty=2,
            meijer_url=f"{PRODUCT}/example-rice/100002.html",
            substitute_ok=False,
            notes="the big bag",
        ),
    )


def test_aliases_split_on_semicolons_and_drop_blank_parts(tmp_path: Path) -> None:  # [R20]
    path = _csv(
        tmp_path,
        [{"name": "olive oil", "category": "staple", "aliases": ";evoo;;extra virgin olive oil;"}],
    )
    (item,) = read_seed_csv(path)
    assert item.aliases == ("evoo", "extra virgin olive oil")


@pytest.mark.parametrize(
    "cells",
    [
        {"substitute_ok": "maybe"},
        {"for_miles": "y"},
        {"substitute_ok": "on"},
        {"interval_days": "eight"},
        {"interval_days": "8.5"},
        {"interval_days": "0"},
        {"last_purchased": "09/01/2026"},
        {"default_qty": "lots"},
        {"default_qty": "0"},
        {"category": "snack"},
        {"name": "  "},
    ],
    ids=[
        "boolean maybe",
        "boolean y",
        "boolean on",
        "interval not a number",
        "interval not whole",
        "interval zero",
        "date not ISO",
        "qty not a number",
        "qty zero",
        "unknown category",
        "blank name",
    ],
)
def test_a_bad_cell_is_an_error_on_its_row(tmp_path: Path, cells: dict[str, str]) -> None:
    # [R20] [R22]
    bad = {"name": "butter", "category": "staple", **cells}
    path = _csv(tmp_path, [{"name": "rice", "category": "staple"}, bad])
    with pytest.raises(SeedError) as excinfo:
        read_seed_csv(path)
    assert _row_numbers(excinfo.value.problems) == {3}


# ── read_seed_csv: Meijer URLs (the meijer.com-only boundary) ─────────────────


@pytest.mark.parametrize(
    ("cell", "stored"),
    [
        (f"{PRODUCT}/café-bustelo/100010.html", f"{PRODUCT}/caf%C3%A9-bustelo/100010.html"),
        (
            "https://www.meijer.com/shopping/search.html?q=jalapeño",
            "https://www.meijer.com/shopping/search.html?q=jalape%C3%B1o",
        ),
        (f"{PRODUCT}/caf%C3%A9-bustelo/100010.html", f"{PRODUCT}/caf%C3%A9-bustelo/100010.html"),
    ],
    ids=["non-ASCII path", "non-ASCII query", "already encoded, left alone"],
)
def test_raw_non_ascii_in_a_url_is_percent_encoded_as_utf8(
    tmp_path: Path, cell: str, stored: str
) -> None:  # [R21]
    path = _csv(tmp_path, [{"name": "coffee", "category": "staple", "meijer_url": cell}])
    (item,) = read_seed_csv(path)
    assert item.meijer_url == stored


@pytest.mark.parametrize(
    "url",
    [
        "https://www.walmart.com/ip/olive-oil/100001",
        "http://www.meijer.com/shopping/product/example-olive-oil/100001.html",
        f"{PRODUCT}/example olive oil/100001.html",
        f"{PRODUCT}\\example-olive-oil/100001.html",
        "https://www.meijer.com\\@evil.example/olive-oil",
        "https://steve@www.meijer.com/shopping/product/example-olive-oil/100001.html",
        "https://www.meijer.com@evil.example/olive-oil",
        "https://www.meıjer.com/shopping/product/example-olive-oil/100001.html",
    ],
    ids=[
        "another host",
        "http",
        "whitespace",
        "backslash in the path",
        "backslash before the host",
        "userinfo",
        "meijer.com as userinfo",
        "look-alike host (dotless i)",
    ],
)
def test_an_unsafe_url_is_an_error_on_its_row(tmp_path: Path, url: str) -> None:  # [R21] [R22]
    rows = [
        {"name": "rice", "category": "staple", "meijer_url": f"{PRODUCT}/example-rice/100002.html"},
        {"name": "olive oil", "category": "staple", "meijer_url": url},
    ]
    with pytest.raises(SeedError) as excinfo:
        read_seed_csv(_csv(tmp_path, rows))
    assert _row_numbers(excinfo.value.problems) == {3}


def test_every_bad_row_is_reported_with_its_row_number(tmp_path: Path) -> None:  # [R22]
    rows = [
        {"name": "rice", "category": "staple"},
        {"name": "butter", "category": "staple", "substitute_ok": "maybe"},
        {"name": "tahini", "category": "staple"},
        {
            "name": "olive oil",
            "category": "staple",
            "interval_days": "ten",
            "meijer_url": "https://evil.example/olive-oil",
        },
        {"name": "chicken nuggets", "category": "snack"},
    ]
    with pytest.raises(SeedError) as excinfo:
        read_seed_csv(_csv(tmp_path, rows))
    assert _row_numbers(excinfo.value.problems) == {3, 5, 6}
    assert isinstance(excinfo.value, ValueError)


# ── staples_due on seed data (PLAN.md, Concurrency lanes: Lane B done-when) ──


def test_staples_due_is_right_on_the_seed_fixture(db: sqlite3.Connection) -> None:  # [R23]
    pantry = SqlitePantry(db)
    assert pantry.load_seed(read_seed_csv(FIXTURE)).inserted == FIXTURE_NAMES
    # On 2026-09-26, days past the ask date (last purchase + 90% of the interval, rounded up):
    # olive oil 14, tahini 6, butter 0. Not due: taco shells (ask date 7 days away), rice (26
    # away), couscous (no purchase date). Never due: eggs (perishable) and chicken nuggets
    # (fallback, though 43 days past).
    assert [item.name for item in pantry.staples_due(ON)] == ["olive oil", "tahini", "butter"]


# ── main (the CLI) ───────────────────────────────────────────────────────────


def test_main_loads_the_csv_prints_counts_and_names_and_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # [R24] [R16]
    db_path = _cli_db(tmp_path, "rice")
    assert main([str(FIXTURE), "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"\b7\b", out), out  # inserted: all but rice
    assert re.search(r"\b1\b", out), out  # updated: rice
    for name in FIXTURE_NAMES:
        assert name in out
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pantry_item").fetchone() == (8,)
        # a seed purchase row per inserted item with a date: not couscous (blank), not rice (updated)
        assert conn.execute("SELECT source FROM purchase_log").fetchall() == [("seed",)] * 6


def test_main_prints_every_bad_row_to_stderr_writes_nothing_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # [R24] [R22]
    db_path = _cli_db(tmp_path, "couscous")
    rows = [
        {"name": "rice", "category": "staple"},
        {"name": "butter", "category": "staple", "for_miles": "maybe"},
        {"name": "tahini", "category": "snack"},
    ]
    csv_path = _csv(tmp_path, rows)
    before = _dump(db_path)
    assert main([str(csv_path), "--db", str(db_path)]) == 1
    err = capsys.readouterr().err
    assert {int(n) for n in re.findall(r"\brow (\d+)\b", err, re.IGNORECASE)} == {3, 4}, err
    assert _dump(db_path) == before


def test_main_prints_a_namespace_clash_to_stderr_writes_nothing_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # [R24] [R14]
    db_path = _cli_db(tmp_path, "tahini")
    rows = [
        {"name": "rice", "category": "staple"},
        {"name": "sesame paste", "category": "staple", "aliases": "tahini"},
    ]
    csv_path = _csv(tmp_path, rows)
    before = _dump(db_path)
    assert main([str(csv_path), "--db", str(db_path)]) == 1
    assert "tahini" in capsys.readouterr().err.casefold()
    assert _dump(db_path) == before
