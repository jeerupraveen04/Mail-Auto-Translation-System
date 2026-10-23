"""Shared fixtures: fake translation engines, FastAPI TestClient, DB store."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.adapters.base import (
    LanguageUnsupported,
    TranslationEngine,
    TranslationError,
)
from app.cache import TranslationCache
from app.config import settings
from app.db import TemplateStore
from app.main import create_app
from app.pipeline import TranslationPipeline
from app.worker import TranslationWorkerPool
from fastapi.testclient import TestClient

# Representative sample text per script family (one per tested script).
SCRIPT_SAMPLES = {
    "es": "¡Hola! Este es un aviso del curso.",
    "fr": "Bonjour ! Ceci est une notification de cours.",
    "de": "Hallo! Dies ist eine Kursbenachrichtigung.",
    "ru": "Привет! Это уведомление о курсе.",
    "zh-CN": "你好！这是课程通知。",
    "zh-TW": "你好！這是課程通知。",
    "ja": "こんにちは！これはコースのお知らせです。",
    "ko": "안녕하세요! 이것은 과정 알림입니다.",
    "hi": "नमस्ते! यह कोर्स सूचना है।",
    "ar": "مرحبًا! هذا إشعار بالدورة.",
    "he": "שלום! זו הודעת קורס.",
    "th": "สวัสดี! นี่คือการแจ้งเตือนหลักสูตร",
    "bn": "নমস্কার! এটি একটি কোর্স বিজ্ঞপ্তি।",
    "ta": "வணக்கம்! இது ஒரு பாடநெறி அறிவிப்பு.",
    "vi": "Xin chào! Đây là thông báo khóa học.",
    "el": "Γεια σας! Αυτή είναι μια ειδοποίηση μαθήματος.",
    "tr": "Merhaba! Bu bir kurs bildirimidir.",
    "pl": "Cześć! To jest powiadomienie o kursie.",
}


class FakeEngine(TranslationEngine):
    """Configurable in-memory engine used to drive the waterfall tests.

    `fail_for` marks locales that should raise; `fail_times` raises the
    first N calls regardless of locale (e.g. simulating a DeepL 429);
    `mangle_tokens` lowercases token casing post-translation (simulating
    engine drift that unmasking must tolerate)."""

    name = "Fake"
    tier = 1
    available = True
    supports_html = True

    def __init__(
        self,
        name="Fake",
        tier=1,
        fail_for: set[str] | None = None,
        fail_times: int = 0,
        error: Exception | None = None,
        mangle_tokens: bool = False,
        unsupported: set[str] | None = None,
        available: bool = True,
    ):
        self.name = name
        self.tier = tier
        self.fail_for = fail_for or set()
        self.fail_times = fail_times
        self.error = error or TranslationError(f"{name} simulated failure")
        self.mangle_tokens = mangle_tokens
        self.unsupported = unsupported or set()
        self.available = available
        self.calls: list[tuple[str, str]] = []

    def translate(self, text: str, target_code: str) -> str:
        self.calls.append((target_code, text))
        if target_code in self.unsupported:
            raise LanguageUnsupported(f"{self.name} cannot handle {target_code}")
        if self.fail_times > 0 or target_code in self.fail_for:
            if self.fail_times > 0:
                self.fail_times -= 1
            raise self.error
        sample = SCRIPT_SAMPLES.get(target_code, f"[{target_code}]")
        translated = f"{sample} {text}"
        if self.mangle_tokens:
            translated = translated.replace("__VAR_TOKEN_", "__Var_Token_")
        return translated


def build_fake_pipeline(fails: dict[str, dict] | None = None) -> tuple[TranslationPipeline, dict[str, FakeEngine]]:
    fails = fails or {}
    engines = {
        "DeepL": FakeEngine(
            name="DeepL", tier=1, available=True, **fails.get("DeepL", {})
        ),
        "AWS": FakeEngine(name="AWS", tier=2, available=True, mangle_tokens=True, **fails.get("AWS", {})),
        "Gemini": FakeEngine(name="Gemini", tier=3, available=True, **fails.get("Gemini", {})),
    }
    breakers = {
        name: __import__("app.circuit_breaker", fromlist=["CircuitBreaker"]).CircuitBreaker(
            name=name, failure_threshold=50
        )
        for name in engines
    }
    pipeline = TranslationPipeline(engines=engines, breakers=breakers)
    return pipeline, engines


@pytest.fixture()
def fake_store(tmp_path):
    store = TemplateStore(settings.database_url)
    store.ensure_schema()
    return store


@pytest.fixture()
def client(fake_store, monkeypatch):
    pipeline, engines = build_fake_pipeline()
    app = create_app()
    test_cache = TranslationCache(redis_url="redis://localhost:6379/0", ttl=60)
    app.state.cache = test_cache
    app.state.pipeline = pipeline
    app.state.engines = engines
    app.state.store = fake_store
    app.state.pool = TranslationWorkerPool(max_workers=10)
    with TestClient(app) as tc:
        yield tc
