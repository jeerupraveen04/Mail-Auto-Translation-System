"""REST API routes (Phase 4/6 backend surface).

Endpoints:
- POST /api/courses/{course_id}/templates/auto-translate   (plan Phase 4)
- GET  /api/courses/{course_id}/templates/{template_key}
- PATCH /api/courses/{course_id}/templates/{template_key}/translations/{locale}
- DELETE /api/courses/{course_id}/templates/{template_key}/translations/{locale}/lock
- GET  /api/languages   (routing matrix for the frontend)
- GET  /api/health
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .cache import source_digest
from .db import TemplateStore
from .languages import DEFAULT_TARGET_CODES, LOCALE_MAP, LOCALES
from .pipeline import TranslationPipeline
from .sanitization import sanitize_text
from .schemas import (
    AutoTranslateRequest,
    AutoTranslateResponse,
    ManualOverrideRequest,
    TranslationEntry,
)

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _services(request: Request) -> tuple[TemplateStore, TranslationPipeline]:
    app = request.app
    return app.state.store, app.state.pipeline


def _to_entry(code: str, payload: dict) -> TranslationEntry:
    rtl = bool(payload.get("rtl", LOCALE_MAP[code].rtl if code in LOCALE_MAP else False))
    return TranslationEntry(
        subject=payload.get("subject", ""),
        body=payload.get("body", ""),
        engine=payload.get("engine"),
        tier=payload.get("tier"),
        rtl=rtl,
        latency_ms=int(payload.get("latency_ms", 0) or 0),
        manual_review=bool(payload.get("manual_review", False)),
        manual_lock=bool(payload.get("manual_lock", False)),
        sync_status=payload.get("sync_status", "manually_edited" if payload.get("manual_lock") else "in_sync"),
        error=payload.get("error"),
        cached=bool(payload.get("cached", False)),
        attempts=list(payload.get("attempts", [])),
        edited_at=payload.get("edited_at"),
    )


@router.get("/health")
def health(request: Request) -> dict:
    engines = {}
    for name, engine in request.app.state.pipeline.engines.items():
        breaker = request.app.state.pipeline.breakers[name]
        engines[name] = {
            "configured": engine.available,
            "circuit": breaker.state,
            "failures": breaker.failure_count,
        }
    cache_ok = bool(request.app.state.cache._get_client())
    db_ok = True
    try:
        with request.app.state.store.engine.connect():
            pass
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ok", "engines": engines, "cache": cache_ok, "database": db_ok}


@router.get("/languages")
def languages() -> dict:
    return {
        "total": len(LOCALES),
        "rtl": [loc.code for loc in LOCALES if loc.rtl],
        "locales": [
            {
                "code": loc.code,
                "name": loc.name,
                "rtl": loc.rtl,
                "cascade": list(loc.cascade),
            }
            for loc in LOCALES
        ],
    }


@router.post("/courses/{course_id}/templates/auto-translate", response_model=AutoTranslateResponse)
def auto_translate(course_id: str, payload: AutoTranslateRequest, request: Request) -> AutoTranslateResponse:
    store, pipeline = _services(request)
    cache = request.app.state.cache
    pool = request.app.state.pool

    target_codes = payload.target_codes or list(DEFAULT_TARGET_CODES)
    unknown = [c for c in target_codes if c not in LOCALE_MAP]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported target_codes: {unknown}")

    template_key = payload.template_key
    sanitized_subject = sanitize_text(payload.subject)
    sanitized_body = sanitize_text(payload.body)

    start = time.monotonic()
    # ---- Cache verification layer ----
    cached: dict[str, dict] = {}
    audit: list[dict[str, Any]] = []
    if not payload.force_refresh:
        cached = cache.get_batch(sanitized_subject, sanitized_body, target_codes)
        for code in cached:
            audit.append(
                {
                    "locale": code,
                    "resolved_engine": cached[code].get("engine"),
                    "cached": True,
                    "latency_ms": cached[code].get("latency_ms", 0),
                    "timestamp": time.time(),
                    "digest": source_digest(sanitized_subject, sanitized_body),
                }
            )
    digest = source_digest(sanitized_subject, sanitized_body)

    # ---- Locked manual overrides are never overwritten ----
    existing_translations: dict[str, dict] = {}
    if payload.persist:
        existing = store.fetch_template(course_id, template_key)
        if existing:
            existing_translations = dict(existing["translations"])
    lock_map = existing_translations
    manual_locked = {c: e for c, e in existing_translations.items() if e.get("manual_lock")}

    pending = [
        c
        for c in target_codes
        if c not in cached and c not in manual_locked
    ]

    translations = dict(existing_translations)
    translations.update(cached)
    translations.update(manual_locked)

    # ---- Parallel cascade over the worker pool ----
    fresh: dict[str, dict] = {}
    if pending:
        fresh = pool.map_parallel(
            lambda code: {
                code: _record_with_digest(
                    pipeline.translate_one_masked(sanitized_subject, sanitized_body, code), digest
                )
            },
            pending,
        )
        for code, record in fresh.items():
            audit.append(
                {
                    "locale": code,
                    "resolved_engine": record.get("engine"),
                    "error": record.get("error"),
                    "manual_review": record.get("manual_review", False),
                    "cached": False,
                    "latency_ms": record.get("latency_ms", 0),
                    "timestamp": time.time(),
                    "digest": digest,
                }
            )
        cache.set_batch(sanitized_subject, sanitized_body, fresh)

    for code, record in fresh.items():
        existing_entry = lock_map.get(code) or {}
        if existing_entry.get("manual_lock"):
            continue
        translations[code] = {
            **(existing_entry if existing_entry.get("latency_ms") else {}),
            **record,
            "manual_lock": False,
            "sync_status": "in_sync",
        }

    total_latency = int((time.monotonic() - start) * 1000)

    # ---- Persist (`merge_translations` preserves locked manual edits) ----
    if payload.persist:
        merged = store.merge_translations(translations, {}, lock_map)
        store.upsert_template(
            course_id=course_id,
            template_key=template_key,
            english_subject=sanitized_subject,
            english_body=sanitized_body,
            translations=merged,
        )

    return AutoTranslateResponse(
        course_id=course_id,
        template_key=template_key,
        translations={code: _to_entry(code, rec) for code, rec in translations.items()},
        audit=[{**a, "batch_latency_ms": total_latency} for a in audit],
    )


def _record_with_digest(record: dict, digest: str) -> dict:
    record = dict(record)
    record["digest"] = digest
    return record


@router.get("/courses/{course_id}/templates/{template_key}")
def get_template(course_id: str, template_key: str, request: Request) -> dict:
    store, _pipeline = _services(request)
    record = store.fetch_template(course_id, template_key)
    if record is None:
        raise HTTPException(status_code=404, detail="template not found")
    record["translations"] = {code: _to_entry(code, e) for code, e in record["translations"].items()}
    return record


@router.patch("/courses/{course_id}/templates/{template_key}/translations/{locale_code}")
def manual_override(
    course_id: str,
    template_key: str,
    locale_code: str,
    payload: ManualOverrideRequest,
    request: Request,
) -> dict:
    store, _pipeline = _services(request)
    if locale_code not in LOCALE_MAP:
        raise HTTPException(status_code=404, detail=f"unknown locale {locale_code}")
    existing = store.fetch_template(course_id, template_key)
    if existing is None:
        raise HTTPException(status_code=404, detail="template not found")
    translations = store.update_single_translation(
        course_id, template_key, locale_code, sanitize_text(payload.subject), sanitize_text(payload.body)
    )
    return {
        "course_id": course_id,
        "template_key": template_key,
        "locale": locale_code,
        "sync_status": translations[locale_code]["sync_status"],
        "translations": {code: _to_entry(code, e) for code, e in translations.items()},
    }


@router.delete("/courses/{course_id}/templates/{template_key}/translations/{locale_code}/lock")
def reset_lock(course_id: str, template_key: str, locale_code: str, request: Request) -> dict:
    store, _pipeline = _services(request)
    result = store.reset_manual_lock(course_id, template_key, locale_code)
    if result is None:
        raise HTTPException(status_code=404, detail="template not found")
    return {
        "course_id": course_id,
        "template_key": template_key,
        "locale": locale_code,
        "sync_status": result.get(locale_code, {}).get("sync_status", "in_sync"),
        "translations": {code: _to_entry(code, e) for code, e in result.items()},
    }
