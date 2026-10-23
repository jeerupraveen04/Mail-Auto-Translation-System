"""Phase 4 tests: cache layer (SHA-256 keys, 30-day TTL, batch ops)."""

from __future__ import annotations

import pytest
from app.cache import TranslationCache, cache_key, source_digest


def test_digest_deterministic_and_field_sensitive():
    d1 = source_digest("Subject", "Body")
    d2 = source_digest("Subject", "Body")
    assert d1 == d2
    assert source_digest("SubjectB", "ody") != d1
    assert source_digest("Subject", "Body2") != d1
    assert len(d1) == 64


def test_cache_key_format():
    key = cache_key("deadbeef", "es")
    assert key.startswith("mtrans:v1:deadbeef:es")


@pytest.fixture()
def redis_available():
    try:
        import redis

        client = redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def test_set_and_get_batch_roundtrip(redis_available):
    if not redis_available:
        pytest.skip("Redis not running")
    cache = TranslationCache(redis_url="redis://localhost:6379/0", ttl=120)
    records = {
        "es": {"subject": "¡Hola!", "body": "Cuerpo", "engine": "DeepL", "tier": 1, "latency_ms": 5},
        "hi": {"subject": "नमस्ते", "body": "संदेश", "engine": "AWS", "tier": 2, "latency_ms": 8},
    }
    cache.set_batch("Subject A", "Body A", records)
    fetched = cache.get_batch("Subject A", "Body A", ["es", "hi"])
    assert fetched["es"]["subject"] == "¡Hola!"
    assert fetched["es"]["engine"] == "DeepL"
    assert fetched["es"]["cached"] is True
    assert fetched["hi"]["engine"] == "AWS"


def test_get_batch_miss_returns_empty(redis_available):
    if not redis_available:
        pytest.skip("Redis not running")
    cache = TranslationCache(redis_url="redis://localhost:6379/0", ttl=120)
    assert cache.get_batch("never-translated subject", "body", ["es"]) == {}


def test_invalidate_clears_entries(redis_available):
    if not redis_available:
        pytest.skip("Redis not running")
    cache = TranslationCache(redis_url="redis://localhost:6379/0", ttl=120)
    cache.set_batch("S", "B", {"sv": {"subject": "Hej", "body": "b", "engine": "DeepL"}})
    assert "sv" in cache.get_batch("S", "B", ["sv"])
    cache.invalidate("S", "B", ["sv"])
    assert cache.get_batch("S", "B", ["sv"]) == {}


def test_unreachable_redis_degrades_to_noop():
    cache = TranslationCache(redis_url="redis://localhost:59199/0")
    assert cache.get_batch("s", "b", ["es"]) == {}
    assert cache._get_client() is None
    cache.set_batch("s", "b", {"es": {"subject": "x", "body": "y"}})  # must not raise


def test_ttl_is_30_day_default():
    cache = TranslationCache()
    assert cache._ttl == 30 * 24 * 3600
