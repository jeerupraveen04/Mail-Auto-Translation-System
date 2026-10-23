"""Circuit breaker guarding each translation provider in the cascade.

States: CLOSED (healthy) -> OPEN (failing, calls skipped instantly)
-> HALF_OPEN (probing call allowed). Prevents a dead tier 1 provider
from adding timeout latency to every request.
"""

from __future__ import annotations

import threading
import time


class CircuitOpenError(RuntimeError):
    def __init__(self, engine: str):
        super().__init__(f"Circuit breaker OPEN for {engine}")
        self.engine = engine


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, reset_seconds: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.failure_count = 0
        self.opened_at: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_at is None:
                return "closed"
            if time.monotonic() - self.opened_at >= self.reset_seconds:
                return "half_open"
            return "open"

    def allow(self) -> bool:
        return self.state in {"closed", "half_open"}

    def record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.opened_at = None

    def record_failure(self, error: str) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_error = error
            if self.failure_count >= self.failure_threshold and self.opened_at is None:
                self.opened_at = time.monotonic()

    def record_attempt_started(self) -> None:
        """A probe succeeded in half-open state is registered via
        record_success; nothing needed here yet."""
