"""Rate-limit / brute-force protection tests, backed by fakeredis."""
import fakeredis.aioredis
import pytest

from src.app.services import rate_limit_service


@pytest.fixture(autouse=True)
def _fake_valkey(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rate_limit_service, "_get_valkey", lambda: fake)
    yield fake


class TestRateLimit:
    async def test_not_blocked_initially(self):
        assert not await rate_limit_service.is_blocked("account:test@example.com", 5)

    async def test_blocked_after_max_attempts(self):
        key = "account:test@example.com"
        for _ in range(5):
            await rate_limit_service.record_failure(key)
        assert await rate_limit_service.is_blocked(key, 5)

    async def test_not_blocked_below_max_attempts(self):
        key = "account:test@example.com"
        for _ in range(4):
            await rate_limit_service.record_failure(key)
        assert not await rate_limit_service.is_blocked(key, 5)

    async def test_reset_clears_the_counter(self):
        key = "account:test@example.com"
        for _ in range(5):
            await rate_limit_service.record_failure(key)
        assert await rate_limit_service.is_blocked(key, 5)
        await rate_limit_service.reset(key)
        assert not await rate_limit_service.is_blocked(key, 5)

    async def test_different_keys_are_independent(self):
        for _ in range(5):
            await rate_limit_service.record_failure("account:attacker-target@example.com")
        assert await rate_limit_service.is_blocked("account:attacker-target@example.com", 5)
        assert not await rate_limit_service.is_blocked("account:unrelated-user@example.com", 5)

    async def test_retry_after_seconds_reflects_ttl(self):
        key = "account:test@example.com"
        await rate_limit_service.record_failure(key, window=100)
        retry_after = await rate_limit_service.retry_after_seconds(key)
        assert 0 < retry_after <= 100

    async def test_retry_after_zero_when_no_record(self):
        assert await rate_limit_service.retry_after_seconds("account:never-tried@example.com") == 0


class TestClientIp:
    def test_extracts_client_host(self):
        class FakeRequest:
            class client:
                host = "203.0.113.42"
        assert rate_limit_service.client_ip(FakeRequest()) == "203.0.113.42"

    def test_returns_unknown_when_no_client(self):
        class FakeRequest:
            client = None
        assert rate_limit_service.client_ip(FakeRequest()) == "unknown"
