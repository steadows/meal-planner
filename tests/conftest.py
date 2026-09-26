"""Shared test fixtures.

Lanes: append your fixtures in your own headed block at the bottom. Don't edit existing blocks;
ask the contracts lane instead.
"""

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from meals.config import Settings, get_settings
from meals.contracts import Ingredient, PantryItem, RecipeOption
from meals.db import get_db
from meals.fakes import FakeClaudeRunner, FakeMealieClient, FakePantry

# ── contracts ────────────────────────────────────────────────────────────────

TODAY = date(2026, 9, 26)


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def sample_recipe() -> RecipeOption:
    return RecipeOption(
        name="Sheet-pan chicken fajitas",
        url="https://example.com/sheet-pan-fajitas",
        source="example.com",
        hands_on_min=20,
        servings=4,
        batch_ok=True,
        fit_note="Miles-friendly with peppers on the side",
        ingredients=(
            Ingredient(name="chicken thighs", qty=2, unit="lb"),
            Ingredient(name="bell peppers", qty=3, unit=None),
            Ingredient(name="corn tortillas", qty=12, unit=None),
        ),
        steps=("Slice everything.", "Roast at 425F for 25 minutes."),
    )


def _item(id_: int, name: str, category: str, **fields: object) -> PantryItem:
    return PantryItem.model_validate({"id": id_, "name": name, "category": category, **fields})


@pytest.fixture
def sample_pantry_items() -> tuple[PantryItem, ...]:
    """Staples due on TODAY, most overdue first: butter (flagged), tahini (2.0x), olive oil (0.93x)."""
    return (
        _item(
            1,
            "olive oil",
            "staple",
            aliases=("evoo",),
            typical_interval_days=70,
            last_purchased=TODAY - timedelta(days=65),
        ),
        _item(
            2,
            "rice",
            "staple",
            typical_interval_days=56,
            last_purchased=TODAY - timedelta(days=20),
        ),
        _item(
            3,
            "butter",
            "staple",
            status="buy_next_time",
            typical_interval_days=21,
            last_purchased=TODAY - timedelta(days=5),
        ),
        _item(
            4,
            "tahini",
            "staple",
            typical_interval_days=60,
            last_purchased=TODAY - timedelta(days=120),
        ),
        _item(5, "eggs", "perishable", aliases=("egg",)),
        _item(6, "chicken nuggets", "fallback", aliases=("nuggets",), for_miles=True),
    )


@pytest.fixture
def fake_claude() -> FakeClaudeRunner:
    return FakeClaudeRunner()


@pytest.fixture
def fake_mealie(sample_recipe: RecipeOption) -> FakeMealieClient:
    return FakeMealieClient(
        recipes={"sheet-pan-chicken-fajitas": sample_recipe},
        tags={"rotation": ("sheet-pan-chicken-fajitas",)},
    )


@pytest.fixture
def fake_pantry(sample_pantry_items: tuple[PantryItem, ...]) -> FakePantry:
    return FakePantry(sample_pantry_items)


# ── contracts: settings isolation and db ─────────────────────────────────────

# Every env var Settings reads. test_config pins the field set against .env.example.
SETTINGS_ENV_KEYS = tuple(name.upper() for name in Settings.model_fields)


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No Settings env var inherited from the shell, and no cached Settings before or after.

    get_settings() still reads a project-root `.env` if one exists; env vars a test sets win over it.
    """
    for key in SETTINGS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    _clear_settings_cache()
    yield
    _clear_settings_cache()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated pantry database on tmp_path. The real schema; there is no DB fake."""
    conn = get_db(tmp_path / "pantry.sqlite")
    yield conn
    conn.close()
