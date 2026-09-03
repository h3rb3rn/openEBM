"""error_tracking.py must never crash startup and must stay a no-op when
SENTRY_DSN isn't configured — this app has to keep working fully
air-gapped/offline with no mandatory external service."""
from src.app.services import error_tracking


def test_init_is_noop_without_dsn(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(error_tracking.settings, "sentry_dsn", "")
    with caplog.at_level(logging.INFO):
        error_tracking.init_error_tracking()  # must not raise
    assert "disabled" in caplog.text.lower()


def test_init_calls_sentry_sdk_when_dsn_configured(monkeypatch):
    import sys
    import types

    calls = {}
    fake_module = types.ModuleType("sentry_sdk")
    fake_module.init = lambda **kwargs: calls.update(kwargs)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_module)

    monkeypatch.setattr(error_tracking.settings, "sentry_dsn", "https://key@example.org/1")
    monkeypatch.setattr(error_tracking.settings, "environment", "test")

    error_tracking.init_error_tracking()

    assert calls["dsn"] == "https://key@example.org/1"
    assert calls["environment"] == "test"
    assert calls["send_default_pii"] is False
