"""Environment config, read from `.env` at the project root. Only the contracts lane edits this file."""

from functools import cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """One field per `.env.example` key, plus `claude_lock_dir`.

    - Secrets are `SecretStr | None`. Each consumer checks the ones it needs at its own startup.
    - `telegram_allowed_chat_id` is an int, as Telegram sends it (negative for group chats).
    - A blank value (`KEY=`) means unset, so a copied `.env.example` never yields an empty token.
    - Relative paths resolve against PROJECT_ROOT, not the working directory, so cron and the bot
      share one database.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_id: int | None = None
    mealie_url: str = "http://localhost:9925"
    mealie_token: SecretStr | None = None
    pantry_db: Path = Path("data/pantry.sqlite")
    prefs_file: Path = Path("meals/prefs.yaml")
    claude_bin: str = "claude"
    claude_max_concurrent: int = Field(default=2, ge=1)
    claude_lock_dir: Path = Path("data/locks")
    chrome_host: str | None = None
    whisper_model: str = "base"
    instacart_api_key: SecretStr | None = None

    @field_validator("pantry_db", "prefs_file", "claude_lock_dir")
    @classmethod
    def _anchor_to_project_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path


@cache
def get_settings() -> Settings:
    """The process-wide Settings, loaded once. Tests reset it with `get_settings.cache_clear()`."""
    return Settings()
