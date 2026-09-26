"""Load the seed CSV, the ingredient → Meijer product map, into the pantry (PLAN.md, Phase 3).

    uv run python -m meals.seed_loader seed/pantry.csv [--db PATH]

The CSV has a header row. `name` and `category` are required; the other columns are optional:

    name, category, aliases, interval_days, last_purchased, default_qty, default_unit,
    meijer_product_id, meijer_url, preferred_product_name, substitute_ok, for_miles, notes

- `category`: staple, perishable or fallback. `aliases`: `;`-separated.
- `interval_days`: a first guess, in days. `last_purchased`: an ISO date (2026-09-26), and only a
  real purchase date. Leave it blank for something that was already in the house.
- `substitute_ok`, `for_miles`: 1/0, yes/no or true/false.
- `meijer_url`: a meijer.com https URL. Raw non-ASCII characters are percent-encoded here, and the
  URL is then held to the meijer.com-only rule the cart relies on.

A blank cell takes the default. Every bad row is reported, and nothing loads until the file is clean.
Re-running updates the product map of items already loaded, but never what the pantry has learned.

An entry point: it wires the database to the pantry, so it may import both.
"""

import argparse
import csv
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from meals.db import get_db
from meals.pantry import SeedItem, SqlitePantry

REQUIRED_COLUMNS = ("name", "category")
OPTIONAL_COLUMNS = (
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
_BOOLEANS = {"1": True, "yes": True, "true": True, "0": False, "no": False, "false": False}
_FIELD_FOR_COLUMN = {"interval_days": "typical_interval_days"}


class SeedError(ValueError):
    """The seed CSV can't be loaded. `problems` has one line per bad header or row ("row 3: ...",
    counting the header as row 1)."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


def _percent_encode_non_ascii(url: str) -> str:
    """Percent-encode raw non-ASCII characters (as UTF-8) and nothing else, so whitespace,
    backslashes and userinfo still reach the meijer.com-only check unchanged."""
    return "".join(quote(char, safe="") if ord(char) > 0x7F else char for char in url)


def _parse_cell(column: str, cell: str) -> object:
    if column == "aliases":
        return tuple(alias.strip() for alias in cell.split(";") if alias.strip())
    if column in ("substitute_ok", "for_miles"):
        if cell.casefold() not in _BOOLEANS:
            raise ValueError(f"{column} must be 1/0, yes/no or true/false, got {cell!r}")
        return _BOOLEANS[cell.casefold()]
    if column == "last_purchased":
        try:
            return date.fromisoformat(cell)
        except ValueError:
            raise ValueError(
                f"last_purchased must be an ISO date (2026-09-26), got {cell!r}"
            ) from None
    if column == "meijer_url":
        return _percent_encode_non_ascii(cell)
    return cell


def _parse_row(row: Mapping[str | None, str | list[str] | None]) -> SeedItem:
    """One data row to a SeedItem. Raises ValueError (or ValidationError) naming the bad cells."""
    fields: dict[str, object] = {}
    for column, raw in row.items():
        if column is None or isinstance(raw, list):  # csv puts cells past the header under None
            raise ValueError("more cells than the header has columns (quote a cell with a comma)")
        cell = (raw or "").strip()
        if cell:
            fields[_FIELD_FOR_COLUMN.get(column, column)] = _parse_cell(column, cell)
    return SeedItem.model_validate(fields)


def _describe(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in detail['loc']) or 'row'}: {detail['msg']}"
            for detail in error.errors()
        )
    return str(error)


def _header_problems(header: Sequence[str]) -> list[str]:
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    unknown = [c for c in header if c not in REQUIRED_COLUMNS and c not in OPTIONAL_COLUMNS]
    repeated = sorted({column for column in header if header.count(column) > 1})
    return (
        [f"row 1 (header): missing column {column!r}" for column in missing]
        + [f"row 1 (header): unknown column {column!r}" for column in unknown]
        + [f"row 1 (header): column {column!r} appears more than once" for column in repeated]
    )


def read_seed_csv(path: Path) -> tuple[SeedItem, ...]:
    """Read and validate the seed CSV (UTF-8, with or without Excel's BOM), all rows or none.

    Raises SeedError listing every bad header column or row.
    """
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, strict=True)  # a bad quote is an error, not a merged row
        header = [column.strip() for column in reader.fieldnames or ()]
        problems = _header_problems(header)
        if problems:
            raise SeedError(problems)
        reader.fieldnames = header
        items: list[SeedItem] = []
        try:
            for row in reader:
                try:
                    items.append(_parse_row(row))
                except ValueError as error:
                    # The file line the row ends on (header = row 1), so blank lines count.
                    problems.append(f"row {reader.line_num}: {_describe(error)}")
        except csv.Error as error:
            # The reader stops counting inside the bad record, so point at where it starts.
            problems.append(
                f"row {reader.line_num + 1}: malformed CSV from here on ({error}); "
                "check for an unclosed quote"
            )
    if problems:
        raise SeedError(problems)
    return tuple(items)


def main(argv: Sequence[str] | None = None) -> int:
    """Load a seed CSV into the pantry database. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m meals.seed_loader", description="Load the seed CSV into the pantry."
    )
    parser.add_argument("csv", type=Path, help="the seed CSV")
    parser.add_argument("--db", type=Path, help="pantry database (default: PANTRY_DB from .env)")
    args = parser.parse_args(argv)
    try:
        items = read_seed_csv(args.csv)
        conn = get_db(args.db)
        try:
            result = SqlitePantry(conn).load_seed(items)
        finally:
            conn.close()
    except SeedError as error:
        for problem in error.problems:
            print(problem, file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"{args.csv}: {error}", file=sys.stderr)
        return 1
    for label, names in (
        ("inserted", result.inserted),
        ("updated", result.updated),
        ("not in the CSV, left unchanged", result.untouched),
    ):
        print(f"{label} {len(names)}" + (f": {', '.join(names)}" if names else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
