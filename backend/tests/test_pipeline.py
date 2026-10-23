"""Phase 2/3 tests: adapters, waterfall cascade, failover, audit metadata,
circuit breaker behavior."""

from __future__ import annotations

import re

import pytest
from app.adapters.base import LanguageUnsupported, TranslationError
from app.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.languages import LOCALE_MAP, LOCALES
from app.pipeline import TranslationPipeline

from tests.conftest import FakeEngine, build_fake_pipeline


def test_deepl_target_code_mapping():
    from app.adapters.deepl_engine import DeepLEngine

    engine = DeepLEngine(api_key="dummy")
    assert engine._target_code("zh-CN") == "ZH-HANS"
    assert engine._target_code("zh-TW") == "ZH-HANT"
    assert engine._target_code("no") == "NB"
    assert engine._target_code("pt") == "PT-PT"
    assert engine._target_code("es") == "ES"


def test_deepl_unsupported_locales_raise_language_unsupported():
    from app.adapters.deepl_engine import DEEPL_SUPPORTED, DeepLEngine

    engine = DeepLEngine(api_key="dummy")
    for code in ("hi", "vi", "th", "he", "ms", "bn", "ta"):
        assert code not in DEEPL_SUPPORTED
        with pytest.raises(LanguageUnsupported):
            engine.translate("text", code)


def test_cascade_primary_engine_matches_routing_matrix():
    pipeline, engines = build_fake_pipeline()
    for locale_spec in LOCALES:
        results = pipeline.translate_batch(
            "Subject {{student_name}}",
            "Body {{course_title}}",
            [locale_spec.code],
        )
        record = results[locale_spec.code]
        primary = locale_spec.cascade[0]
        assert record["engine"] == primary, f"{locale_spec.code} should resolve to {primary}"
        assert record["tier"] == engines[primary].tier


def test_all_38_locales_translate_with_correct_rtl_flags():
    pipeline, _ = build_fake_pipeline()
    results = pipeline.translate_batch("Hello {{student_name}}", "Course {{course_title}} notification")
    assert len(results) == 38
    for locale_spec in LOCALES:
        entry = results[locale_spec.code]
        assert entry["rtl"] == locale_spec.rtl
        assert entry["engine"] is not None
        assert "{{student_name}}" in entry["subject"]
        assert "{{course_title}}" in entry["body"]
        assert re.search(r"__VAR_TOKEN_\d+__", entry["subject"]) is None
        assert entry["latency_ms"] >= 0


def test_deepl_429_failover_to_aws():
    pipeline, _engines = build_fake_pipeline(
        {"DeepL": {"fail_for": {"es"}, "error": TranslationError("DeepL HTTP 429 quota exceeded")}}
    )
    results = pipeline.translate_batch("Hello {{student_name}}", "Body", ["es"])
    record = results["es"]
    assert record["engine"] == "AWS"
    assert record["tier"] == 2
    assert record["manual_review"] is False
    assert "{{student_name}}" in record["subject"]
    # Audit chain shows the DeepL failure then the AWS resolution.
    fallback_errors = [a["error"] for a in record["attempts"] if a["error"]]
    assert any("429" in e for e in fallback_errors)


def test_aws_failure_falls_through_to_gemini():
    pipeline, _ = build_fake_pipeline(
        {
            "DeepL": {"unsupported": {"es"}},  # skip tier 1 for spanish
            "AWS": {"fail_for": {"es"}, "error": TranslationError("AWS ThrottlingException")},
        }
    )
    results = pipeline.translate_batch("Hello {{student_name}}", "Body", ["es"])
    record = results["es"]
    assert record["engine"] == "Gemini"
    assert record["tier"] == 3


def test_unsupported_engine_is_skipped_in_cascade():
    pipeline, _ = build_fake_pipeline({"DeepL": {"unsupported": {"he"}}})
    results = pipeline.translate_batch("Hello {{student_name}}", "Body", ["he"])
    assert results["he"]["engine"] == "AWS"


def test_all_tiers_fail_returns_manual_review_flag():
    pipeline, _ = build_fake_pipeline(
        {
            "DeepL": {"fail_for": {"fr"}},
            "AWS": {"fail_for": {"fr"}},
            "Gemini": {"fail_for": {"fr"}},
        }
    )
    results = pipeline.translate_batch("Hello {{student_name}}", "Body", ["fr"])
    record = results["fr"]
    assert record["engine"] is None
    assert record["manual_review"] is True
    assert record["error"] is not None
    assert "manual review" in record["error"]


def test_missing_credentials_are_skipped_not_crash():
    engines = {
        "DeepL": FakeEngine(name="DeepL", tier=1, available=False),
        "AWS": FakeEngine(name="AWS", tier=2, available=False),
        "Gemini": FakeEngine(name="Gemini", tier=3, available=True),
    }
    pipeline = TranslationPipeline(engines=engines, breakers=None)
    results = pipeline.translate_batch("Hello {{student_name}}", "Body", ["ja"])
    record = results["ja"]
    assert record["engine"] == "Gemini"
    skipped = [a for a in record["attempts"] if a["error"] and "credentials" in a["error"]]
    assert len(skipped) == 2


def test_audit_metadata_present_for_every_locale():
    pipeline, _ = build_fake_pipeline()
    results = pipeline.translate_batch("Subj {{student_name}}", "Body", ["de", "hi"])
    for code, record in results.items():
        assert record["latency_ms"] >= 0
        assert record["tier"] in (1, 2, 3)
        attempts = record["attempts"]
        assert attempts and attempts[-1]["engine"] == record["engine"]
        assert record["rtl"] == LOCALE_MAP[code].rtl


def test_aws_mangled_tokens_are_still_unmasked():
    pipeline, _ = build_fake_pipeline()
    results = pipeline.translate_batch(
        "Hello {{student_name}}, welcome!", "Body with {{course_title}}", ["hi"]
    )
    record = results["hi"]
    assert record["engine"] == "AWS"
    assert "{{student_name}}" in record["subject"]
    assert "{{course_title}}" in record["body"]
    assert "__VAR_TOKEN_" not in record["subject"]


# --------------- Circuit breaker ---------------


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker("test", failure_threshold=3, reset_seconds=0.1)
    for _ in range(3):
        breaker.record_failure("boom")
    assert breaker.state == "open"
    assert breaker.allow() is False
    with pytest.raises(CircuitOpenError):
        if not breaker.allow():
            raise CircuitOpenError("test")


def test_circuit_breaker_half_open_after_reset_window():
    breaker = CircuitBreaker("test", failure_threshold=2, reset_seconds=0.05)
    breaker.record_failure("err")
    breaker.record_failure("err")
    assert breaker.state == "open"
    import time

    time.sleep(0.06)
    assert breaker.state == "half_open"
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state == "closed"


def test_circuit_breaker_skips_dead_tier_without_timeout_comparison():
    """A repeatedly failing DeepL gets circuit-opened; AWS answers and the
    attempt log must record the open state, not a fresh call."""
    pipeline, _engines = build_fake_pipeline({"DeepL": {"fail_for": {"it"}}})
    for _ in range(55):  # threshold 50 in conftest build
        pipeline.translate_batch("s", "b", ["it"])
    record = pipeline.translate_batch("Hello {{student_name}}", "b", ["it"])["it"]
    assert record["engine"] == "AWS"
    open_attempts = [a for a in record["attempts"] if a["error"] and "circuit open" in a["error"]]
    assert open_attempts, "expected circuit-open attempt record"
