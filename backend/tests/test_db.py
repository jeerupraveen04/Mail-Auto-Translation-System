"""Phase 5 tests: PostgreSQL persistence (real schema per plan §5)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("fake_store")


def test_ensure_schema_creates_plan_tables(fake_store):
    with fake_store.engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns WHERE table_name='course_mail_templates'"
            ).fetchall()
        }
    expected = {
        "id", "course_id", "template_key", "source_lang", "english_subject",
        "english_body", "pipeline", "translations", "created_at", "updated_at",
    }
    assert expected <= cols


def test_plan_indexes_exist(fake_store):
    with fake_store.engine.connect() as conn:
        names = {
            r[0]
            for r in conn.exec_driver_sql("SELECT indexname FROM pg_indexes WHERE tablename='course_mail_templates'").fetchall()
        }
    assert "idx_mail_templates_lookup" in names
    assert "idx_mail_templates_gin" in names


def test_upsert_and_fetch_roundtrip(fake_store):
    course_id = f"crs-{uuid.uuid4().hex[:10]}"
    translations = {"es": {"subject": "¡Hola {{student_name}}!", "body": "Bienvenido", "engine": "DeepL", "manual_lock": False}}
    fake_store.upsert_template(course_id, "welcome", "Hello", "Welcome body", translations)
    record = fake_store.fetch_template(course_id, "welcome")
    assert record["course_id"] == course_id
    assert record["english_subject"] == "Hello"
    assert record["translations"]["es"]["subject"] == "¡Hola {{student_name}}!"
    assert record["created_at"] and record["updated_at"]

    # Update overwrites same (course, key) row instead of duplicating.
    fake_store.upsert_template(course_id, "welcome", "Hello v2", "Body v2", {})
    record = fake_store.fetch_template(course_id, "welcome")
    assert record["english_subject"] == "Hello v2"


def test_merge_translations_preserves_manual_lock(fake_store):
    base = {
        "fr": {"subject": "Bonjour", "body": "b", "engine": "DeepL", "manual_lock": True, "sync_status": "manually_edited"},
        "de": {"subject": "Hallo", "body": "b", "engine": "DeepL", "manual_lock": False, "sync_status": "in_sync"},
    }
    updates = {
        "fr": {"subject": "Auto FR", "body": "b", "engine": "AWS"},
        "de": {"subject": "Auto DE", "body": "b", "engine": "AWS"},
        "es": {"subject": "Hola", "body": "b", "engine": "AWS"},
    }
    merged = fake_store.merge_translations(base, updates)
    assert merged["fr"]["subject"] == "Bonjour"  # locked: untouched
    assert merged["fr"]["sync_status"] == "manually_edited"
    assert merged["de"]["subject"] == "Auto DE"  # unlocked: overwritten
    assert merged["es"]["subject"] == "Hola"  # new locale added


def test_manual_override_locks_and_reset_unlocks(fake_store):
    course_id = f"crs-{uuid.uuid4().hex[:10]}"
    translations = {"fr": {"subject": "Bonjour", "body": "b", "engine": "DeepL", "rtl": False, "tier": 1, "manual_lock": False}}
    fake_store.upsert_template(course_id, "note", "Subj", "Body", translations)

    updated = fake_store.update_single_translation(
        course_id, "note", "fr", "Bonjour修订 manual", "body manual"
    )
    assert updated["fr"]["manual_lock"] is True
    assert updated["fr"]["sync_status"] == "manually_edited"
    assert updated["fr"]["subject"] == "Bonjour修订 manual"

    unlocked = fake_store.reset_manual_lock(course_id, "note", "fr")
    assert unlocked["fr"]["manual_lock"] is False
    assert unlocked["fr"]["sync_status"] == "in_sync"


def test_fetch_missing_template_returns_none(fake_store):
    assert fake_store.fetch_template("nope", "missing") is None


def test_jsonb_gin_search_by_engine(fake_store):
    course_id = f"crs-{uuid.uuid4().hex[:10]}"
    translations = {"es": {"subject": "Hola", "body": "b", "engine": "DeepL"}}
    fake_store.upsert_template(course_id, "kw", "s", "b", translations)
    hits = fake_store.jsonb_search(course_id, "DeepL")
    assert "kw" in hits
