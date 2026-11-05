from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://aitrader:aitrader@127.0.0.1:5433/aitrader"
    host: str = "127.0.0.1"
    port: int = 8765
    kite_api_key: str = ""
    kite_api_secret: SecretStr = SecretStr("")
    token_encryption_key: SecretStr = SecretStr("")
    dashboard_url: str = "http://localhost:5173"
    gemini_api_key: SecretStr = SecretStr("")  # AI picks (pretend portfolio only)
    ai_model: str = "gemini-2.5-flash"

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value not in LOOPBACK_HOSTS:
            raise ValueError(f"host must be one of {sorted(LOOPBACK_HOSTS)} (local only), got {value!r}")
        return value

    def kite_configured(self) -> bool:
        return bool(
            self.kite_api_key
            and self.kite_api_secret.get_secret_value()
            and self.token_encryption_key.get_secret_value()
        )


DASHBOARD_ENV = Path(__file__).resolve().parents[2] / "ai-trader" / ".env"


def gemini_key(settings: Settings, fallback: Path = DASHBOARD_ENV) -> str:
    """The core's own GEMINI_API_KEY, else that one line from the dashboard's .env (nothing else is read from it).
    The value is never logged."""
    own = settings.gemini_api_key.get_secret_value()
    if own or not fallback.is_file():
        return own
    for line in fallback.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "GEMINI_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""
