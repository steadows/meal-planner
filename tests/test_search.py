import logging
import re
import shutil
from pathlib import Path

import pytest

from meals import search
from meals.config import get_settings
from meals.contracts import ClaudeRunnerError, RecipeOption
from meals.fakes import FakeClaudeRunner

PROJECT_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.usefixtures("prefs_file")


def _options(sample: RecipeOption, count: int) -> tuple[RecipeOption, ...]:
    return tuple(
        sample.model_copy(update={"name": f"Recipe {n}", "url": f"https://example.com/r{n}"})
        for n in range(count)
    )


def _queue(claude: FakeClaudeRunner, options: tuple[RecipeOption, ...]) -> None:
    claude.queue({"options": [option.model_dump(mode="json") for option in options]})


# ── find ─────────────────────────────────────────────────────────────────────


def test_find_returns_the_options_claude_found(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption
) -> None:
    options = _options(sample_recipe, 3)
    _queue(patched_claude, options)

    assert search.find("a sheet-pan dinner under 30 minutes") == options


def test_find_is_one_structured_web_run_not_chrome(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption
) -> None:
    _queue(patched_claude, _options(sample_recipe, 3))

    search.find("a slow cooker chicken recipe")

    (call,) = patched_claude.calls
    assert call.schema is not None
    assert call.chrome is False


def test_prompt_carries_the_request_and_the_profile(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption, prefs_file: Path
) -> None:
    _queue(patched_claude, _options(sample_recipe, 3))

    search.find("  something with ground turkey that isn't tacos \n")

    (call,) = patched_claude.calls
    assert "something with ground turkey that isn't tacos" in call.prompt
    assert prefs_file.read_text(encoding="utf-8") in call.prompt


def test_profile_is_reread_on_every_search(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption, prefs_file: Path
) -> None:
    _queue(patched_claude, _options(sample_recipe, 3))
    _queue(patched_claude, _options(sample_recipe, 3))

    search.find("chicken")
    prefs_file.write_text("dislikes: [mushrooms-after-edit]\n", encoding="utf-8")
    search.find("chicken")

    first, second = patched_claude.calls
    assert "mushrooms-after-edit" not in first.prompt
    assert "mushrooms-after-edit" in second.prompt


def test_zero_options_is_a_valid_answer(patched_claude: FakeClaudeRunner) -> None:
    _queue(patched_claude, ())

    assert search.find("a dessert made of quinoa") == ()


def test_more_than_five_options_fails_validation(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption
) -> None:
    _queue(patched_claude, _options(sample_recipe, search.MAX_OPTIONS + 1))

    with pytest.raises(ClaudeRunnerError):
        search.find("chicken")


def test_runner_failure_propagates(patched_claude: FakeClaudeRunner) -> None:
    patched_claude.queue(ClaudeRunnerError("claude timed out after 600s"))

    with pytest.raises(ClaudeRunnerError, match="timed out"):
        search.find("chicken")


@pytest.mark.parametrize("request_", ["", "   \n\t"])
def test_blank_request_is_rejected_without_calling_claude(
    patched_claude: FakeClaudeRunner, request_: str
) -> None:
    with pytest.raises(ValueError):
        search.find(request_)

    assert patched_claude.calls == []


def test_oversized_request_is_rejected_without_calling_claude(
    patched_claude: FakeClaudeRunner, sample_recipe: RecipeOption
) -> None:
    _queue(patched_claude, _options(sample_recipe, 3))
    search.find("x" * search.MAX_REQUEST_CHARS)  # at the limit: fine

    with pytest.raises(ValueError):
        search.find("x" * (search.MAX_REQUEST_CHARS + 1))

    assert len(patched_claude.calls) == 1


# ── the profile file ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rule", ["likes", "dislikes", "grains", "wheat_traps", "goals", "effort", "miles", "sources"]
)
def test_shipped_profile_has_every_rule(rule: str) -> None:
    text = (PROJECT_ROOT / "meals" / "prefs.yaml").read_text(encoding="utf-8")

    assert re.search(rf"^{rule}:", text, re.MULTILINE), f"prefs.yaml has no top-level {rule!r}"


# ── live ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_find_live_against_real_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREFS_FILE")
    get_settings.cache_clear()
    if shutil.which(get_settings().claude_bin) is None:
        pytest.skip("claude CLI not installed")

    options = search.find("a sheet-pan chicken dinner under 30 minutes that Miles would eat")

    assert 1 <= len(options) <= search.MAX_OPTIONS
    for option in options:
        assert option.url.startswith(("https://", "http://")), option.url
        assert option.ingredients and option.steps, option.name


def test_find_logs_the_request_and_the_result_count(
    patched_claude: FakeClaudeRunner,
    sample_recipe: RecipeOption,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _queue(patched_claude, _options(sample_recipe, 4))

    with caplog.at_level(logging.INFO, logger="meals.search"):
        search.find("ground turkey, not tacos")

    assert "ground turkey, not tacos" in caplog.text
    assert "4" in caplog.text
