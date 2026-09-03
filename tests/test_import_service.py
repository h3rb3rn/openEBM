"""
Import status state-machine tests, backed by fakeredis instead of a live
Valkey instance.

This guards the fix for a real multi-worker bug found during development:
the app runs with --workers 2 (separate OS processes), so import progress
must live in a shared store (Valkey), not a module-level dict — otherwise
status polling randomly shows stale state depending on which worker
handles a given request. See CHANGELOG "Fixed a latent multi-worker bug".
"""
import fakeredis.aioredis
import pytest

from src.app.services import import_service


@pytest.fixture(autouse=True)
def _fake_valkey(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(import_service, "_get_valkey", lambda: fake)
    yield fake


class TestStatusStateMachine:
    async def test_default_status_is_idle(self):
        status = await import_service._get_status()
        assert status["state"] == "idle"

    async def test_set_status_persists_and_merges(self):
        await import_service._set_status(state="fetching", message="Lade PDF…")
        status = await import_service._get_status()
        assert status["state"] == "fetching"
        assert status["message"] == "Lade PDF…"

    async def test_set_status_merges_rather_than_overwrites(self):
        await import_service._set_status(state="fetching", message="erste Nachricht")
        await import_service._set_status(message="zweite Nachricht")
        status = await import_service._get_status()
        # state from the first call must survive a partial update
        assert status["state"] == "fetching"
        assert status["message"] == "zweite Nachricht"

    async def test_status_visible_across_separate_valkey_handles(self):
        """Simulates two different worker processes: each gets its own
        _get_valkey() call, but since fakeredis is shared per-test via the
        fixture, this stands in for 'status written by worker A is visible
        to worker B' — the exact property the in-memory-dict version broke."""
        await import_service._set_status(state="preview_ready", gop_count=3173)
        status_from_other_handle = await import_service._get_status()
        assert status_from_other_handle["state"] == "preview_ready"
        assert status_from_other_handle["gop_count"] == 3173


class TestKbvFetchGuard:
    async def test_fetch_allowed_when_idle(self, monkeypatch):
        started = {}
        monkeypatch.setattr(
            import_service.asyncio, "create_task", lambda coro: started.setdefault("called", True) or coro.close()
        )
        result = await import_service.start_kbv_fetch_background("https://example.org/catalog.pdf")
        assert result is True
        assert started.get("called") is True

    async def test_fetch_rejected_when_already_fetching(self, monkeypatch):
        await import_service._set_status(state="fetching")
        monkeypatch.setattr(import_service.asyncio, "create_task", lambda coro: coro.close())
        result = await import_service.start_kbv_fetch_background("https://example.org/catalog.pdf")
        assert result is False

    async def test_fetch_rejected_when_committing(self, monkeypatch):
        await import_service._set_status(state="committing")
        monkeypatch.setattr(import_service.asyncio, "create_task", lambda coro: coro.close())
        result = await import_service.start_kbv_fetch_background("https://example.org/catalog.pdf")
        assert result is False


class TestPendingKbvImportStorage:
    async def test_store_and_retrieve_pending_import(self):
        gops = [{"code": "01100", "value_points": 196.0}]
        await import_service._store_pending_kbv_import("run-1", gops)
        retrieved = await import_service._get_pending_kbv_import("run-1")
        assert retrieved == gops

    async def test_missing_run_id_returns_none(self):
        assert await import_service._get_pending_kbv_import("nonexistent-run") is None

    async def test_delete_pending_import(self):
        await import_service._store_pending_kbv_import("run-2", [{"code": "01100"}])
        await import_service._delete_pending_kbv_import("run-2")
        assert await import_service._get_pending_kbv_import("run-2") is None

    async def test_commit_rejected_for_unknown_run_id(self, monkeypatch):
        monkeypatch.setattr(import_service.asyncio, "create_task", lambda coro: coro.close())
        result = await import_service.start_kbv_commit_background("nonexistent-run")
        assert result is False
