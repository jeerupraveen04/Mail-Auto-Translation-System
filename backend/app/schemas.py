"""Pydantic schemas for the REST API (plan Phase 4 request/response)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TranslationEntry(BaseModel):
    subject: str
    body: str
    engine: str | None = None
    tier: int | None = None
    rtl: bool = False
    latency_ms: int = 0
    manual_review: bool = False
    manual_lock: bool = False
    sync_status: str = "in_sync"
    error: str | None = None
    cached: bool = False
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    edited_at: str | None = None


class AutoTranslateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=5000)
    body: str = Field(min_length=1, max_length=50000)
    target_codes: list[str] | None = None
    template_key: str = Field(default="default")
    persist: bool = True
    force_refresh: bool = False


class AutoTranslateResponse(BaseModel):
    course_id: str
    template_key: str
    translations: dict[str, TranslationEntry]
    audit: list[dict[str, Any]] = Field(default_factory=list)


class ManualOverrideRequest(BaseModel):
    subject: str = Field(min_length=0, max_length=10000)
    body: str = Field(min_length=1, max_length=50000)


class TemplateResponse(BaseModel):
    course_id: str
    template_key: str
    english_subject: str
    english_body: str
    pipeline: str
    translations: dict[str, TranslationEntry]
    created_at: str | None = None
    updated_at: str | None = None
