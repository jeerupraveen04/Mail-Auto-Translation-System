"""DeepL adapter (Tier 1).

Uses the official `deepl` SDK with the plan's target-code mapping
(``zh-CN -> ZH-HANS``, ``zh-TW -> ZH-HANT``, ``no -> NB``, ``pt -> PT-PT``),
a connection pool via one shared Translator instance, a bounded semaphore
for concurrency, and exponential backoff on 429 / 5xx.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import settings
from .base import LanguageUnsupported, TranslationEngine, TranslationError

_logger = logging.getLogger(__name__)

# Locale -> DeepL target_lang codes that differ from the bare locale code.
DEEPL_CODE_MAP: dict[str, str] = {
    "zh-CN": "ZH-HANS",
    "zh-TW": "ZH-HANT",
    "no": "NB",
    "pt": "PT-PT",
}

# Locales DeepL can service.
DEEPL_SUPPORTED: frozenset[str] = frozenset(
    {
        "es", "fr", "de", "it", "pt", "ru", "zh-CN", "zh-TW", "ja", "ko",
        "ar", "tr", "nl", "pl", "id", "uk", "sv", "ro", "cs", "el", "hu",
        "da", "fi", "no", "sk", "bg", "hr", "lt", "sl", "lv", "et",
    }
)

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# Bounded concurrency shared across all DeepL calls (plan §4: ≤50 provider
# connections vs. our 10-worker pool).
TRANSLATE_SEMAPHORE = threading.BoundedSemaphore(10)


class DeepLEngine(TranslationEngine):
    name = "DeepL"
    tier = 1
    supports_html = True

    def __init__(self, api_key: str | None = None, max_concurrency: int = 10):
        self._api_key = api_key if api_key is not None else settings.deepl_api_key
        self._max_concurrency = max_concurrency
        self._lock = threading.Lock()
        self._translator = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise TranslationError("DEEPL_API_KEY not configured")
        with self._lock:
            if self._translator is None:
                import deepl

                self._translator = deepl.Translator(
                    self._api_key,
                    timeout=settings.engine_timeout_seconds * 2,
                    max_retries=settings.max_retries,
                )
            return self._translator

    def _target_code(self, target_code: str) -> str:
        code = DEEPL_CODE_MAP.get(target_code, target_code.upper())
        return code

    def translate(self, text: str, target_code: str) -> str:
        if target_code not in DEEPL_SUPPORTED:
            raise LanguageUnsupported(f"DeepL does not support locale {target_code!r}")
        translator = self._client()
        backoff = 0.4
        last_error: Exception | None = None
        for attempt in range(1, settings.max_retries + 1):
            start = time.monotonic()
            try:
                with TRANSLATE_SEMAPHORE:
                    result = translator.translate_text(
                        text,
                        target_lang=self._target_code(target_code),
                        tag_handling=None,
                    )
                _logger.debug("DeepL ok in %.0f ms", self._elapsed(start))
                return result.text
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                _logger.warning("DeepL attempt %d failed (%s)", attempt, exc)
                if status in RETRYABLE_HTTP_CODES or isinstance(exc, (TimeoutError, ConnectionError)):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 4.0)
                    continue
                if status == 403:
                    raise TranslationError(f"DeepL auth/quota failure: {exc}") from exc
                raise TranslationError(f"DeepL failure: {exc}") from exc
        raise TranslationError(f"DeepL retries exhausted: {last_error}")
