import pytest

from aitrader import __main__ as cli


def test_serve_refuses_non_loopback_host(monkeypatch, capsys):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(cli, "uvicorn", None)  # would crash if serve got as far as starting
    assert cli.main(["serve"]) == 2
    assert "local only" in capsys.readouterr().err


def test_unknown_job_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        cli.main(["run-job", "place_order"])


def test_live_check_without_kite_config_explains(monkeypatch, capsys):
    for key in ("KITE_API_KEY", "KITE_API_SECRET", "TOKEN_ENCRYPTION_KEY"):
        monkeypatch.setenv(key, "")
    assert cli.main(["live-check"]) == 1
    assert "not configured" in capsys.readouterr().out
