"""Shared adapter interface for the 3-tier translation cascade (Phase 2)."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field


class TranslationError(Exception):
    """A provider call failed (network, HTTP, rate limit, timeout, …)."""


class LanguageUnsupported(TranslationError):
    """The provider cannot translate into the requested target locale."""


@dataclass
class AttemptRecord:
    engine: str
    tier: int
    error: str | None = None
    latency_ms: int = 0


@dataclass
class EngineResult:
    text: str
    engine: str
    tier: int
    latency_ms: int
    attempts: list[AttemptRecord] = field(default_factory=list)
    error: str | None = None
    manual_review: bool = False


class TranslationEngine(abc.ABC):
    """Common interface so the waterfall can walk the tiers uniformly."""

    name: str = "abstract"
    tier: int = 0
    supports_html: bool = True

    @abc.abstractmethod
    def translate(self, text: str, target_code: str) -> str:
        """Translate masked text into `target_code`. Raises
        TranslationError / LanguageUnsupported on failure."""

    @staticmethod
    def _elapsed(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
