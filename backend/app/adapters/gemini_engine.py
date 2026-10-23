"""Google Gemini adapter (Tier 3).

Uses the `google-genai` SDK targeting `gemini-1.5-flash` for
high-throughput, low-latency translation with the plan's system prompt.
Handles every locale in the matrix; other tiers fall back into it.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import settings
from .base import TranslationEngine, TranslationError

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = (
    "Translate the educational notification into {target_language}. "
    "Maintain exact casing for code variables matching __VAR_TOKEN_X__. "
    "Do not add explanations; output only the translation."
)


class GeminiEngine(TranslationEngine):
    name = "Gemini"
    tier = 3
    supports_html = False

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key if api_key is not None else settings.gemini_api_key
        self._model = model if model is not None else settings.gemini_model
        self._lock = threading.Lock()
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ai_client(self):
        if not self.available:
            raise TranslationError("GEMINI_API_KEY not configured")
        with self._lock:
            if self._client is None:
                from google import genai

                self._client = genai.Client(api_key=self._api_key)
            return self._client

    def translate(self, text: str, target_code: str) -> str:
        client = self._ai_client()
        prompt = SYSTEM_PROMPT_TEMPLATE.format(target_language=target_code)
        backoff = 0.5
        start = time.monotonic()
        max_attempts = max(settings.max_retries, 2)
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.models.generate_content(
                    model=self._model,
                    contents=[{"role": "user", "parts": [{"text": text}]}],
                    config={
                        "system_instruction": prompt,
                        "temperature": 0.0,
                        "max_output_tokens": 2048,
                    },
                )
                translated = (getattr(response, "text", None) or "").strip()
                if not translated:
                    raise TranslationError("Gemini returned an empty completion")
                _logger.debug("Gemini ok in %.0f ms", self._elapsed(start))
                return translated
            except TranslationError:
                raise
            except Exception as exc:
                _logger.warning("Gemini attempt %d failed (%s)", attempt, exc)
                if attempt < max_attempts:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 4.0)
                    continue
                raise TranslationError(f"Gemini failure: {exc}") from exc
        raise TranslationError("Gemini retries exhausted")
