"""Phase 4: background worker pool.

38 concurrent translations run on a ThreadPoolExecutor (default 10
workers per plan §4) so the FastAPI thread never blocks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config import settings

_logger = logging.getLogger(__name__)


class TranslationWorkerPool:
    def __init__(self, max_workers: int | None = None):
        self._max_workers = max_workers or settings.worker_pool_size
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="mtrans-worker")

    def map_parallel(self, fn: Callable[[Any], dict[str, dict]], items: list) -> dict[str, dict]:
        """Run `fn(item)` for each item concurrently; merge dicts by key."""
        merged: dict[str, dict] = {}
        futures = {self._executor.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                merged.update(future.result())
            except Exception:
                _logger.exception("Worker crashed for item %r", item)
        return merged

    def run_batch(self, fn, items: list) -> list:
        futures = [self._executor.submit(fn, item) for item in items]
        return [f.result() for f in futures]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
