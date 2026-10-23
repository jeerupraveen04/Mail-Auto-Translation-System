"""Phase 5: PostgreSQL persistence layer.

Schema mirrors plan.md §5: a JSONB `translations` column, a composite
lookup index and a GIN index for querying translation contents.
All mutations upsert on (course_id, template_key) so a course template
has exactly one row per (course, key)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import create_engine, text

from .config import settings

_logger = logging.getLogger(__name__)

UPSERT_SQL = """
INSERT INTO course_mail_templates
    (course_id, template_key, source_lang, english_subject, english_body, pipeline, translations, created_at, updated_at)
VALUES
    (:course_id, :template_key, 'en', :english_subject, :english_body, :pipeline, CAST(:translations AS jsonb), NOW(), NOW())
ON CONFLICT ON CONSTRAINT uniq_course_template
DO UPDATE SET
    english_subject = EXCLUDED.english_subject,
    english_body = EXCLUDED.english_body,
    pipeline = EXCLUDED.pipeline,
    translations = EXCLUDED.translations,
    updated_at = NOW()
"""

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS course_mail_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id VARCHAR(64) NOT NULL,
    template_key VARCHAR(64) NOT NULL,
    source_lang VARCHAR(8) DEFAULT 'en',
    english_subject TEXT NOT NULL,
    english_body TEXT NOT NULL,
    pipeline VARCHAR(64) DEFAULT 'DeepL > AWS > Gemini',
    translations JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uniq_course_template UNIQUE (course_id, template_key)
);

CREATE INDEX IF NOT EXISTS idx_mail_templates_lookup
    ON course_mail_templates(course_id, template_key);

CREATE INDEX IF NOT EXISTS idx_mail_templates_gin
    ON course_mail_templates USING gin(translations);
"""


class TemplateStore:
    """Synchronous repository used from FastAPI threadpool handlers."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or settings.database_url
        self._engine = create_engine(self.database_url, pool_pre_ping=True, pool_size=5)

    @property
    def engine(self):
        return self._engine

    def database_url_hostport(self) -> str:
        return self.database_url.split("://", 1)[-1].split("/", 1)[0]

    def ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text(MIGRATION_SQL))
        _logger.info("course_mail_templates schema ensured")

    def upsert_template(
        self,
        course_id: str,
        template_key: str,
        english_subject: str,
        english_body: str,
        translations: dict,
        pipeline: str = "DeepL > AWS > Gemini",
    ) -> dict:
        with self._engine.begin() as conn:
            conn.execute(
                text(UPSERT_SQL),
                {
                    "course_id": course_id,
                    "template_key": template_key,
                    "english_subject": english_subject,
                    "english_body": english_body,
                    "pipeline": pipeline,
                    "translations": json.dumps(translations),
                },
            )
        return {"course_id": course_id, "template_key": template_key}

    def fetch_template(self, course_id: str, template_key: str) -> dict | None:
        stmt = text(
            "SELECT id, course_id, template_key, source_lang, english_subject, english_body,"
            " pipeline, translations, created_at, updated_at"
            " FROM course_mail_templates WHERE course_id = :cid AND template_key = :tk LIMIT 1"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"cid": course_id, "tk": template_key}).mappings().first()
        if row is None:
            return None
        record = dict(row)
        record["translations"] = json.loads(record["translations"]) if isinstance(record["translations"], str) else record["translations"]
        for ts_field in ("created_at", "updated_at"):
            record[ts_field] = record[ts_field].isoformat() if record[ts_field] else None
        return record

    def fetch_translations(self, course_id: str, template_key: str) -> dict | None:
        record = self.fetch_template(course_id, template_key)
        if record is None:
            return None
        return {
            "translations": record["translations"],
            "english_subject": record["english_subject"],
            "english_body": record["english_body"],
            "updated_at": record["updated_at"],
        }

    def merge_translations(
        self,
        base: dict,
        updates: dict,
        lock_map: dict | None = None,
    ) -> dict:
        """Merge fresh auto-translations without clobbering locked
        manual overrides (plan Phase 6 / QA checklist item 4).
        `lock_map` entries with manual_lock=True are immune; entries
        with manual_lock=True also inherit `manual_review=False`."""
        merged = dict(base)
        lock_source = lock_map if lock_map is not None else base
        for locale_code, record in (updates or {}).items():
            info = lock_source.get(locale_code) or {}
            if info.get("manual_lock"):
                locked = {**info, "manual_review": False}
                merged[locale_code] = locked
                continue
            merged[locale_code] = {
                **(info or {}),
                **record,
                "manual_lock": False,
                "sync_status": "in_sync",
            }
        return merged

    def update_single_translation(
        self,
        course_id: str,
        template_key: str,
        locale_code: str,
        subject: str,
        body: str,
    ) -> dict | None:
        record = self.fetch_template(course_id, template_key)
        if record is None:
            return None
        translations = record["translations"]
        entry = translations.get(locale_code) or {}
        entry.update(
            {
                "subject": subject,
                "body": body,
                "engine": entry.get("engine"),
                "tier": entry.get("tier"),
                "rtl": entry.get("rtl", False),
                "manual_review": False,
                "manual_lock": True,
                "sync_status": "manually_edited",
                "edited_at": datetime.now(UTC).isoformat(),
            }
        )
        translations[locale_code] = entry
        self.upsert_template(
            course_id, template_key, record["english_subject"], record["english_body"], translations
        )
        return translations

    def reset_manual_lock(self, course_id: str, template_key: str, locale_code: str) -> dict | None:
        record = self.fetch_template(course_id, template_key)
        if record is None:
            return None
        translations = record["translations"]
        if locale_code in translations:
            translations[locale_code]["manual_lock"] = False
            translations[locale_code]["sync_status"] = "in_sync"
            self.upsert_template(
                course_id, template_key, record["english_subject"], record["english_body"], translations
            )
        return translations

    def jsonb_search(self, course_id: str, engine: str) -> list[str]:
        """Demonstrates the GIN index (plan §5): locales resolved by a
        given engine for a course via @> containment query."""
        stmt = text(
            "SELECT template_key FROM course_mail_templates"
            " WHERE course_id = :cid AND translations @> CAST(:probe AS jsonb)"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {"cid": course_id, "probe": json.dumps({"es": {"engine": engine}})},
            ).scalars().all()
        return list(rows)


def migration_statements() -> str:
    return MIGRATION_SQL
