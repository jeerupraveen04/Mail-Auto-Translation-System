"""Phase 3: Cascading waterfall across DeepL -> AWS -> Gemini with circuit
breaker logic, audit metadata, shielding and sanitization integration."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from .adapters.aws_engine import AWSTranslateEngine
from .adapters.base import (
    AttemptRecord,
    TranslationEngine,
    TranslationError,
)
from .adapters.deepl_engine import DeepLEngine
from .adapters.gemini_engine import GeminiEngine
from .circuit_breaker import CircuitBreaker
from .config import settings
from .languages import DEFAULT_TARGET_CODES, LOCALE_MAP, LOCALES
from .sanitization import sanitize_text
from .shielding import (
    compute_shielded_chunks,
    strip_translate_no_wrappers,
    unmask_tokens,
)

_logger = logging.getLogger(__name__)


class TranslationPipeline:
    """Walks the cascade for each target locale, translating subject and
    body with variable shielding, circuit breakers and audit metadata."""

    def __init__(
        self,
        engines: dict[str, TranslationEngine] | None = None,
        breakers: dict[str, CircuitBreaker] | None = None,
    ):
        if engines is None:
            engines = {
                "DeepL": DeepLEngine(max_concurrency=settings.worker_pool_size),
                "AWS": AWSTranslateEngine(),
                "Gemini": GeminiEngine(),
            }
        self.engines = engines
        if breakers is None:
            breakers = {
                name: CircuitBreaker(
                    name=name,
                    failure_threshold=settings.breaker_failure_threshold,
                    reset_seconds=settings.breaker_reset_seconds,
                )
                for name in engines
            }
        self.breakers = breakers

    def translate_one(
        self,
        subject_masked: str,
        body_masked: str,
        token_map: dict[str, str],
        target_code: str,
        html_mode: bool = False,
    ) -> dict:
        """Run the waterfall for a single locale and return the audit-rich
        translation record with placeholders restored."""
        cascade = LOCALE_MAP[target_code].cascade
        attempts: list[AttemptRecord] = []
        rtl = LOCALE_MAP[target_code].rtl

        for engine_name in cascade:
            engine = self.engines[engine_name]
            breaker = self.breakers[engine_name]

            if not engine.available:
                attempts.append(AttemptRecord(engine=engine_name, tier=engine.tier, error="credentials not configured"))
                continue
            if not breaker.allow():
                attempts.append(
                    AttemptRecord(engine=engine_name, tier=engine.tier, error=f"circuit open (last error: {breaker.last_error})")
                )
                _logger.info("Skipping %s for %s: circuit open", engine_name, target_code)
                continue

            start = time.monotonic()
            try:
                subject_out = engine.translate(subject_masked, target_code)
                body_out = engine.translate(body_masked, target_code)
                latency_ms = int((time.monotonic() - start) * 1000)
                breaker.record_success()
                record = self._compose_entry(
                    text_subject=subject_out,
                    text_body=body_out,
                    token_map=token_map,
                    html_mode=html_mode,
                    engine_name=engine_name,
                    tier=engine.tier,
                    target_code=target_code,
                    rtl=rtl,
                    latency_ms=latency_ms,
                    attempts=[*attempts, AttemptRecord(engine=engine_name, tier=engine.tier, latency_ms=0)],
                )
                record["attempts"] = [asdict(a) for a in record.pop("attempt_records")]
                return record
            except TranslationError as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                breaker.record_failure(str(exc))
                attempts.append(AttemptRecord(engine=engine_name, tier=engine.tier, error=str(exc), latency_ms=latency_ms))
                _logger.warning("%s failed for %s: %s", engine_name, target_code, exc)
                continue

        return {
            "subject": unmask_tokens(subject_masked, token_map),
            "body": unmask_tokens(body_masked, token_map),
            "engine": None,
            "tier": None,
            "rtl": rtl,
            "latency_ms": sum(a.latency_ms for a in attempts),
            "manual_review": True,
            "error": "all tiers failed; flagged for manual review",
            "attempts": [asdict(a) for a in attempts],
        }

    def _compose_entry(
        self,
        *,
        text_subject: str,
        text_body: str,
        token_map: dict[str, str],
        html_mode: bool,
        engine_name: str,
        tier: int,
        target_code: str,
        rtl: bool,
        latency_ms: int,
        attempts: list[AttemptRecord],
    ) -> dict:
        subject_restored = unmask_tokens(text_subject, token_map)
        body_restored = unmask_tokens(text_body, token_map)
        if html_mode:
            subject_restored = strip_translate_no_wrappers(subject_restored)
            body_restored = strip_translate_no_wrappers(body_restored)
        return {
            "subject": sanitize_text(subject_restored),
            "body": sanitize_text(body_restored),
            "token_map": token_map,
            "engine": engine_name,
            "tier": tier,
            "rtl": rtl,
            "latency_ms": latency_ms,
            "manual_review": False,
            "error": None,
            "attempt_records": attempts,
            "attempts": [],
        }

    def translate_one_masked(
        self,
        subject: str,
        body: str,
        target_code: str,
        html_mode: bool = False,
    ) -> dict:
        """Convenience worker entrypoint: mask + cascade + unmask for one
        locale, returning the public record (no token internals)."""
        subject_masked, body_masked, token_map = compute_shielded_chunks(subject, body, html_mode=html_mode)
        record = self.translate_one(subject_masked, body_masked, token_map, target_code, html_mode=html_mode)
        return {k: v for k, v in record.items() if k != "token_map"}

    def translate_batch(
        self,
        subject: str,
        body: str,
        target_codes: list[str] | tuple[str, ...] | None = None,
        html_mode: bool = False,
    ) -> dict[str, dict]:
        """Mask once, then translate via the engine-native MIME mode."""
        targets = list(target_codes) if target_codes else list(DEFAULT_TARGET_CODES)
        subject_masked, body_masked, token_map = compute_shielded_chunks(subject, body, html_mode=html_mode)
        results: dict[str, dict] = {}
        for code in targets:
            results[code] = self.translate_one(subject_masked, body_masked, token_map, code, html_mode=html_mode)
        # Strip raw internals from the public payload.
        public = {}
        for code, record in results.items():
            entry = {k: v for k, v in record.items() if k not in {"token_map", "attempt_records", "attempts"}}
            entry["attempts"] = record.get("attempts") or [
                asdict(a) for a in record.get("attempt_records", [])
            ]
            public[code] = entry
        return public


def describe_locales() -> list[dict]:
    return [
        {"code": loc.code, "name": loc.name, "rtl": loc.rtl, "cascade": list(loc.cascade)}
        for loc in LOCALES
    ]
