"""Fakes for every shared contract. Test-only: the import-linter layering keeps production code out."""

from meals.fakes.claude import ClaudeCall, FakeClaudeRunner
from meals.fakes.mealie import FakeMealieClient
from meals.fakes.pantry import FakePantry

__all__ = ["ClaudeCall", "FakeClaudeRunner", "FakeMealieClient", "FakePantry"]
