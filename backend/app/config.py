"""Central configuration (env-driven; secrets must come from the
environment / vault — never hardcoded, per plan.md §5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(env: str) -> bool:
    return bool(os.getenv(env, "").strip())


@dataclass
class Settings:
    # --- Translation provider credentials (stage via vault / env) ---
    deepl_api_key: str = field(default_factory=lambda: os.getenv("DEEPL_API_KEY", ""))
    aws_access_key_id: str = field(default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID", ""))
    aws_secret_access_key: str = field(default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY", ""))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "ap-south-1"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    # --- Engine tuning ---
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
    engine_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("ENGINE_TIMEOUT_SECONDS", "3.0")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("ENGINE_MAX_RETRIES", "3")))

    # --- Waterfall / circuit breaker ---
    breaker_failure_threshold: int = field(default_factory=lambda: int(os.getenv("BREAKER_FAILURE_THRESHOLD", "5")))
    breaker_reset_seconds: float = field(default_factory=lambda: float(os.getenv("BREAKER_RESET_SECONDS", "60")))

    # --- Concurrency ---
    worker_pool_size: int = field(default_factory=lambda: int(os.getenv("WORKER_POOL_SIZE", "10")))

    # --- Cache ---
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("CACHE_TTL_SECONDS", str(30 * 24 * 3600))))

    # --- Persistence ---
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg2://edunexa:edunexa@localhost:55432/mail_translation"
        )
    )

    @property
    def deepl_enabled(self) -> bool:
        return _flag("DEEPL_API_KEY")

    @property
    def aws_enabled(self) -> bool:
        return _flag("AWS_ACCESS_KEY_ID") and _flag("AWS_SECRET_ACCESS_KEY")

    @property
    def gemini_enabled(self) -> bool:
        return _flag("GEMINI_API_KEY")


settings = Settings()
