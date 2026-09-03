"""
Auth unit tests — no live DB/Redis/Neo4j needed, pure logic.

_extract_token's cookie-vs-header priority is a regression test for a real
production incident: the function used to check only the Authorization
header, so the app was stuck in a login<->dashboard redirect loop for every
browser user once the frontend moved to httpOnly-cookie sessions. See
CHANGELOG "reload loop" fix.
"""
from starlette.requests import Request

from src.app.api.auth import (
    SESSION_COOKIE_NAME,
    _extract_token,
    create_2fa_pending_token,
    create_access_token,
    hash_password,
    verify_password,
)


def _make_request(cookie: str | None = None, auth_header: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", f"{SESSION_COOKIE_NAME}={cookie}".encode()))
    if auth_header:
        headers.append((b"authorization", auth_header.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    return Request(scope)


class TestExtractToken:
    def test_cookie_only(self):
        req = _make_request(cookie="cookie-token-value")
        assert _extract_token(req) == "cookie-token-value"

    def test_bearer_header_only(self):
        req = _make_request(auth_header="Bearer header-token-value")
        assert _extract_token(req) == "header-token-value"

    def test_cookie_takes_priority_over_header(self):
        """The exact bug: browser sends both an (old/unrelated) header and
        the session cookie. The cookie — the actual current session — must
        win, not be silently ignored in favor of a header."""
        req = _make_request(cookie="cookie-token-value", auth_header="Bearer header-token-value")
        assert _extract_token(req) == "cookie-token-value"

    def test_neither_present_returns_none(self):
        req = _make_request()
        assert _extract_token(req) is None

    def test_malformed_auth_header_ignored(self):
        req = _make_request(auth_header="NotBearer sometoken")
        assert _extract_token(req) is None

    def test_bearer_case_insensitive(self):
        req = _make_request(auth_header="bearer lowercase-scheme-token")
        assert _extract_token(req) == "lowercase-scheme-token"


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed)

    def test_wrong_password_rejected(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert not verify_password("wrong-password", hashed)

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert hashed != "correct-horse-battery-staple"


class TestTokens:
    def test_access_token_roundtrip(self):
        from jose import jwt
        from src.app.config import get_settings

        settings = get_settings()
        token = create_access_token({"sub": "user-123", "tenant_id": "tenant-456"})
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        assert payload["sub"] == "user-123"
        assert payload["tenant_id"] == "tenant-456"
        assert "exp" in payload

    def test_2fa_pending_token_has_restricted_scope(self):
        """A 2fa_pending token must never be usable as a full session token —
        it can only be redeemed at /auth/2fa/verify. This is what
        _user_from_jwt checks and rejects."""
        from jose import jwt
        from src.app.config import get_settings

        settings = get_settings()
        token = create_2fa_pending_token({"sub": "user-123", "tenant_id": "tenant-456"})
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        assert payload["scope"] == "2fa_pending"
