"""meals.config: typed settings from the environment.

Authority: the config.py docstrings, `.env.example`, and the seam map (`Settings` / `get_settings()`).
Every test builds Settings with `_env_file=None` (or `.env.example` itself) so a real `.env` in the
project root can't change the outcome.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from meals import config
from meals.config import Settings, get_settings

pytestmark = pytest.mark.usefixtures("isolated_settings")

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"

SECRETS = ("TELEGRAM_BOT_TOKEN", "MEALIE_TOKEN", "INSTACART_API_KEY")
PATHS = ("PANTRY_DB", "PREFS_FILE", "CLAUDE_LOCK_DIR")
ALL_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_ID",
    "MEALIE_URL",
    "MEALIE_TOKEN",
    "PANTRY_DB",
    "PREFS_FILE",
    "CLAUDE_BIN",
    "CLAUDE_MAX_CONCURRENT",
    "CHROME_HOST",
    "WHISPER_MODEL",
    "INSTACART_API_KEY",
    "CLAUDE_LOCK_DIR",
)


def _secret(value: SecretStr | None) -> str | None:
    return None if value is None else value.get_secret_value()


def test_project_root_is_the_repo_root() -> None:
    assert config.PROJECT_ROOT == ROOT


# ── names ────────────────────────────────────────────────────────────────────


def test_one_field_per_env_example_key_plus_claude_lock_dir() -> None:
    example_keys = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert example_keys | {"CLAUDE_LOCK_DIR"} == set(ALL_KEYS)
    assert set(Settings.model_fields) == {key.lower() for key in ALL_KEYS}


def test_reads_every_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "tg-token-123",
        "TELEGRAM_ALLOWED_CHAT_ID": "123456789",
        "MEALIE_URL": "http://mealie.test:9000",
        "MEALIE_TOKEN": "mealie-token-456",
        "PANTRY_DB": str(tmp_path / "pantry.sqlite"),
        "PREFS_FILE": str(tmp_path / "prefs.yaml"),
        "CLAUDE_BIN": "/opt/claude/bin/claude",
        "CLAUDE_MAX_CONCURRENT": "3",
        "CHROME_HOST": "steve@macbook",
        "WHISPER_MODEL": "small",
        "INSTACART_API_KEY": "instacart-key-789",
        "CLAUDE_LOCK_DIR": str(tmp_path / "locks"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    assert _secret(settings.telegram_bot_token) == "tg-token-123"
    assert settings.telegram_allowed_chat_id == 123456789
    assert str(settings.mealie_url).rstrip("/") == "http://mealie.test:9000"
    assert _secret(settings.mealie_token) == "mealie-token-456"
    assert settings.pantry_db == tmp_path / "pantry.sqlite"
    assert settings.prefs_file == tmp_path / "prefs.yaml"
    assert str(settings.claude_bin) == "/opt/claude/bin/claude"
    assert settings.claude_max_concurrent == 3
    assert settings.chrome_host == "steve@macbook"
    assert settings.whisper_model == "small"
    assert _secret(settings.instacart_api_key) == "instacart-key-789"
    assert settings.claude_lock_dir == tmp_path / "locks"


# ── defaults and blank values ────────────────────────────────────────────────


@pytest.mark.parametrize("env_file", [None, ENV_EXAMPLE], ids=["no_env_file", "copied_env_example"])
def test_defaults_match_env_example(
    env_file: Path | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing set, or a verbatim copy of .env.example: no empty tokens, paths under the repo."""
    monkeypatch.chdir(tmp_path)

    settings = Settings(_env_file=env_file)

    assert settings.telegram_bot_token is None
    assert settings.telegram_allowed_chat_id is None
    assert str(settings.mealie_url).rstrip("/") == "http://localhost:9925"
    assert settings.mealie_token is None
    assert settings.pantry_db == ROOT / "data" / "pantry.sqlite"
    assert settings.prefs_file == ROOT / "meals" / "prefs.yaml"
    assert str(settings.claude_bin) == "claude"
    assert settings.claude_max_concurrent == 2
    assert settings.chrome_host is None
    assert settings.whisper_model == "base"
    assert settings.instacart_api_key is None
    assert isinstance(settings.claude_lock_dir, Path)
    assert settings.claude_lock_dir.is_absolute()


@pytest.mark.parametrize("key", ALL_KEYS)
def test_blank_value_means_unset(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`KEY=` must behave exactly like KEY being absent: SecretStr('') is not None."""
    unset = getattr(Settings(_env_file=None), key.lower())
    monkeypatch.setenv(key, "")

    blank = getattr(Settings(_env_file=None), key.lower())

    assert blank == unset


# ── paths ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", PATHS)
def test_relative_paths_resolve_against_project_root_not_cwd(
    key: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cron starting in $HOME must not create a second database next to itself."""
    elsewhere = tmp_path / "cron-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    monkeypatch.setenv(key, "state/nested/target")
    assert getattr(Settings(_env_file=None), key.lower()) == ROOT / "state" / "nested" / "target"

    absolute = tmp_path / "absolute" / "target"
    monkeypatch.setenv(key, str(absolute))
    assert getattr(Settings(_env_file=None), key.lower()) == absolute


# ── secrets ──────────────────────────────────────────────────────────────────


def test_secrets_are_masked_but_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {key: f"{key.lower()}-sekrit-value" for key in SECRETS}
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    for key, value in values.items():
        assert value not in repr(settings)
        assert value not in str(settings)
        assert _secret(getattr(settings, key.lower())) == value


# ── claude_max_concurrent ────────────────────────────────────────────────────


def test_claude_max_concurrent_must_be_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_MAX_CONCURRENT", "1")
    assert Settings(_env_file=None).claude_max_concurrent == 1

    monkeypatch.setenv("CLAUDE_MAX_CONCURRENT", "0")
    with pytest.raises(ValidationError, match="claude_max_concurrent"):
        Settings(_env_file=None)


# ── get_settings ─────────────────────────────────────────────────────────────


def test_get_settings_is_cached_until_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_MODEL", "small")
    first = get_settings()
    monkeypatch.setenv("WHISPER_MODEL", "medium")

    assert isinstance(first, Settings)
    assert get_settings() is first
    assert first.whisper_model == "small"

    get_settings.cache_clear()
    fresh = get_settings()

    assert fresh is not first
    assert fresh.whisper_model == "medium"


def test_the_dotenv_read_is_the_project_roots_not_the_working_directorys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `.env` is the project root's, so cron and the bot read the same one."""
    env_file = Settings.model_config.get("env_file")
    assert isinstance(env_file, str | Path)
    assert Path(env_file) == ROOT / ".env"

    (tmp_path / ".env").write_text("WHISPER_MODEL=from-the-cwd-dotenv\n")
    monkeypatch.chdir(tmp_path)

    assert get_settings().whisper_model != "from-the-cwd-dotenv"
