"""AWS Translate adapter (Tier 2).

Uses `boto3` with an explicit retry policy (exponential backoff via the
standard re-tryer) and maps BCP 47 locales to AWS Translate language
codes. Handles the locales DeepL cannot (Indic / SE Asian) and is the
first failover tier.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import settings
from .base import LanguageUnsupported, TranslationEngine, TranslationError

_logger = logging.getLogger(__name__)

# BCP 47 -> AWS Translate codes that differ.
AWS_CODE_MAP: dict[str, str] = {
    "zh-CN": "zh",
    "zh-TW": "zh-TW",
    "no": "no",  # AWS auto-maps Norwegian via its documented list
}

AWS_SUPPORTED: frozenset[str] = frozenset(
    {
        "es", "fr", "de", "it", "pt", "ru", "zh-CN", "zh-TW", "ja", "ko",
        "hi", "ar", "tr", "nl", "pl", "id", "vi", "th", "uk", "sv", "ro",
        "cs", "el", "hu", "da", "fi", "no", "sk", "bg", "hr", "lt", "sl",
        "lv", "et", "he", "ms", "bn", "ta",
    }
)


class AWSTranslateEngine(TranslationEngine):
    name = "AWS"
    tier = 2
    supports_html = False

    def __init__(
        self,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str | None = None,
        max_retries: int | None = None,
    ):
        self._access_key_id = access_key_id if access_key_id is not None else settings.aws_access_key_id
        self._secret = secret_access_key if secret_access_key is not None else settings.aws_secret_access_key
        self._region = region if region is not None else settings.aws_region
        self._max_retries = max_retries if max_retries is not None else settings.max_retries
        self._lock = threading.Lock()
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._access_key_id and self._secret)

    def _boto_client(self):
        if not self.available:
            raise TranslationError("AWS credentials not configured")
        with self._lock:
            if self._client is None:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "translate",
                    region_name=self._region,
                    aws_access_key_id=self._access_key_id,
                    aws_secret_access_key=self._secret,
                    config=Config(
                        retries={"max_attempts": self._max_retries, "mode": "adaptive"},
                        connect_timeout=settings.engine_timeout_seconds,
                        read_timeout=settings.engine_timeout_seconds * 3,
                    ),
                )
            return self._client

    def _target_code(self, target_code: str) -> str:
        code = AWS_CODE_MAP.get(target_code, target_code)
        if code not in AWS_SUPPORTED:
            raise LanguageUnsupported(f"AWS Translate does not support locale {target_code!r}")
        return code

    def translate(self, text: str, target_code: str) -> str:
        target = self._target_code(target_code)
        client = self._boto_client()
        backoff = 0.4
        start = time.monotonic()
        for attempt in range(1, self._max_retries + 1):
            try:
                response = client.translate_text(
                    Text=text,
                    SourceLanguageCode="en",
                    TargetLanguageCode=target,
                )
                _logger.debug("AWS ok in %.0f ms", self._elapsed(start))
                return response["TranslatedText"]
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "get", lambda *_: None)("ResponseMetadata", {})
                http_status = (status or {}).get("HTTPStatusCode")
                retryable = http_status in {429, 500, 502, 503, 504} or "Throttling" in type(exc).__name__
                _logger.warning("AWS attempt %d failed (%s)", attempt, exc)
                if not retryable:
                    raise TranslationError(f"AWS Translate failure: {exc}") from exc
                if attempt < self._max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 4.0)
        raise TranslationError("AWS Translate retries exhausted")
