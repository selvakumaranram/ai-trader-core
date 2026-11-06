import pytest
from pydantic import ValidationError

from aitrader.config import Settings


def test_defaults_are_loopback_and_local_db(monkeypatch):
    for key in ("HOST", "DATABASE_URL", "KITE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.kite_configured() is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_non_loopback_host_is_refused(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, host=host)


def test_secrets_never_appear_in_repr():
    settings = Settings(
        _env_file=None,
        kite_api_key="key123",
        kite_api_secret="super-secret-value",
        token_encryption_key="enc-key-value",
    )
    assert "super-secret-value" not in repr(settings)
    assert "enc-key-value" not in str(settings)
    assert settings.kite_configured() is True


def test_gemini_key_falls_back_to_only_that_line_of_the_dashboard_env(tmp_path, monkeypatch):
    from aitrader.config import Settings, gemini_key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    other = tmp_path / "dash.env"
    other.write_text("DATABASE_URL=postgresql://elsewhere\nGEMINI_API_KEY = \"abc123\"\nOPENAI_API_KEY=zzz\n",
                     encoding="utf-8")
    s = Settings(_env_file=None)
    assert gemini_key(s, other) == "abc123"
    assert s.database_url != "postgresql://elsewhere"  # nothing else is read from that file
    assert gemini_key(Settings(_env_file=None, gemini_api_key="own"), other) == "own"
    assert gemini_key(s, tmp_path / "missing.env") == ""
