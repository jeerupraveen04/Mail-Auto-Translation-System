"""Cache verification layer (Phase 4): Redis keyed by
SHA-256(source_text) + target_code, 30-day TTL. Degrades gracefully to a
no-op store when Redis is unreachable so the pipeline still functions."""

from __future__ import annotations

import hashlib
import json
import logging
import threading

from .config import settings

_logger = logging.getLogger(__name__)

KEY_PREFIX = "mtrans:v1"
TTL_30_DAYS = 30 * 24 * 3600


def source_digest(subject: str, body: str) -> str:
    digest = hashlib.sha256()
    digest.update(subject.encode("utf-8"))
    digest.update(b"\x1f")  # unit separator between fields
    digest.update(body.encode("utf-8"))
    return digest.hexdigest()


def cache_key(digest: str, target_code: str) -> str:
    return f"{KEY_PREFIX}:{digest}:{target_code}"


class TranslationCache:
    def __init__(self, redis_url: str | None = None, ttl: int | None = None):
        self._ttl = ttl or TTL_30_DAYS
        self._lock = threading.Lock()
        self._client = None
        self._redis_url = redis_url or settings.redis_url
        self._available_flag: bool | None = None

    def _get_client(self):
        with self._lock:
            if self._available_flag is False:
                return None
            if self._client is None:
                try:
                    import redis

                    client = redis.Redis.from_url(self._redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
                    client.ping()
                    self._client = client
                    self._available_flag = True
                except Exception as exc:  # noqa: BLE001 - any Redis error disables cache
                    _logger.warning("Redis cache unavailable, running without cache: %s", exc)
                    self._available_flag = False
                    return None
            return self._client

    def get_batch(self, subject: str, body: str, target_codes: list[str]) -> dict[str, dict]:
        """Return cached translation records for the given locale subset."""
        digest = source_digest(subject, body)
        client = self._get_client()
        if client is None:
            return {}
        try:
            pipe = client.pipeline()
            for code in target_codes:
                pipe.get(cache_key(digest, code))
            rows = pipe.execute()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Cache read failed: %s", exc)
            return {}
        results: dict[str, dict] = {}
        for code, row in zip(target_codes, rows):
            if row:
                try:
                    data = json.loads(row)
                    data["cached"] = True
                    results[code] = data
                except json.JSONDecodeError:
                    continue
        return results

    def set_batch(self, subject: str, body: str, records: dict[str, dict]) -> None:
        digest = source_digest(subject, body)
        client = self._get_client()
        if client is None:
            return
        try:
            pipe = client.pipeline()
            for code, record in records.items():
                payload = {k: v for k, v in record.items() if k not in {"cached"}}
                pipe.setex(cache_key(digest, code), self._ttl, json.dumps(payload))
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Cache write failed: %s", exc)

    def invalidate(self, subject: str, body: str, target_codes: list[str]) -> None:
        digest = source_digest(subject, body)
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(*[cache_key(digest, code) for code in target_codes])
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Cache invalidation failed: %s", exc)
