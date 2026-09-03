"""
email_service tests. The critical property: with no SMTP configured
(the air-gapped/default case), sending must be a safe no-op — never an
exception that could break the request handling it's called from.
"""
from src.app.services import email_service


def test_send_email_is_noop_without_smtp_host(monkeypatch):
    monkeypatch.setattr(email_service.settings, "smtp_host", "")
    result = email_service.send_email("user@example.org", "Subject", "Body")
    assert result is False


def test_send_email_attempts_delivery_when_configured(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, msg):
            sent["sendmail"] = (from_addr, to_addrs)

    monkeypatch.setattr(email_service.settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(email_service.settings, "smtp_port", 587)
    monkeypatch.setattr(email_service.settings, "smtp_user", "svc@example.org")
    monkeypatch.setattr(email_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(email_service.settings, "smtp_use_tls", True)
    monkeypatch.setattr(email_service.settings, "smtp_from", "EBM <noreply@example.org>")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    result = email_service.send_email("user@example.org", "Subject", "Body")

    assert result is True
    assert sent["host"] == "smtp.example.org"
    assert sent["starttls"] is True
    assert sent["login"] == ("svc@example.org", "secret")
    assert sent["sendmail"][1] == ["user@example.org"]


def test_send_email_returns_false_on_smtp_error(monkeypatch):
    class FailingSMTP:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(email_service.settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)

    result = email_service.send_email("user@example.org", "Subject", "Body")
    assert result is False


def test_password_reset_email_includes_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        email_service, "send_email",
        lambda to, subject, body: captured.update(to=to, subject=subject, body=body) or True,
    )
    email_service.send_password_reset_email("user@example.org", "https://example.org/login?reset_token=abc123")
    assert captured["to"] == "user@example.org"
    assert "https://example.org/login?reset_token=abc123" in captured["body"]
