"""API + end-to-end tests (Phase 4 REST surface, Phase 6 lock semantics,
QA checklist: variable preservation, failover, export integrity)."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def course_id():
    return f"test-course-{uuid.uuid4().hex[:8]}"


def test_health_reports_components(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    for engine in ("DeepL", "AWS", "Gemini"):
        assert engine in body["engines"]


def test_languages_endpoint_returns_38_locales(client):
    resp = client.get("/api/languages")
    body = resp.json()
    assert body["total"] == 38
    assert set(body["rtl"]) == {"ar", "he"}
    codes = [loc["code"] for loc in body["locales"]]
    assert codes == [
        "es", "fr", "de", "it", "pt", "ru", "zh-CN", "zh-TW", "ja", "ko",
        "hi", "ar", "tr", "nl", "pl", "id", "vi", "th", "uk", "sv", "ro",
        "cs", "el", "hu", "da", "fi", "no", "sk", "bg", "hr", "lt", "sl",
        "lv", "et", "he", "ms", "bn", "ta",
    ]


def test_auto_translate_all_38_languages(client, course_id):
    resp = client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={
            "subject": "Welcome {{student_name}}!",
            "body": "Course {{course_title}} starts {{start_date}}.",
            "template_key": "welcome",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    translations = body["translations"]
    assert len(translations) == 38

    # QA checklist: variable shielding across all 38 outputs
    for entry in translations.values():
        assert "{{student_name}}" in entry["subject"]
        assert "__VAR_TOKEN" not in entry["subject"]
        assert entry["engine"] in ("DeepL", "AWS", "Gemini")
        assert entry["manual_review"] is False

    # RTL flags
    assert translations["ar"]["rtl"] is True
    assert translations["he"]["rtl"] is True
    assert translations["es"]["rtl"] is False

    # Engine badges per routing matrix
    assert translations["es"]["engine"] == "DeepL"
    assert translations["hi"]["engine"] == "AWS"

    # Audit trail
    assert body["audit"], "audit metadata expected"
    for item in body["audit"]:
        assert item["resolved_engine"] is not None
        assert "timestamp" in item and "latency_ms" in item

    # Export integrity: JSON payload strictly matches schema keys
    entry = translations["es"]
    assert set(entry.keys()) == {
        "subject", "body", "engine", "tier", "rtl", "latency_ms",
        "manual_review", "manual_lock", "sync_status", "error", "cached",
        "attempts", "edited_at",
    }


def test_cached_hits_on_second_call(client, course_id):
    payload = {
        "subject": "Cache check {{student_name}}",
        "body": "Body {{course_title}}",
        "template_key": "t",
    }
    first = client.post(f"/api/courses/{course_id}/templates/auto-translate", json=payload).json()
    second = client.post(f"/api/courses/{course_id}/templates/auto-translate", json=payload).json()
    assert second["translations"]["es"]["cached"] is True
    assert second["translations"]["es"]["subject"] == first["translations"]["es"]["subject"]


def test_force_refresh_bypasses_cache(client, course_id):
    payload = {"subject": "Force {{student_name}}", "body": "Body", "template_key": "t", "force_refresh": False}
    client.post(f"/api/courses/{course_id}/templates/auto-translate", json=payload)
    payload["force_refresh"] = True
    again = client.post(f"/api/courses/{course_id}/templates/auto-translate", json=payload).json()
    assert again["translations"]["es"]["cached"] is False


def test_manual_override_locked_survives_resync(client, course_id):
    # First sync
    client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "Hello {{student_name}}", "body": "Body", "template_key": "k"},
    )
    # Instructor manually refines French
    resp = client.patch(
        f"/api/courses/{course_id}/templates/k/translations/fr",
        json={"subject": "Refined French greeting", "body": "Refined French body"},
    )
    assert resp.status_code == 200
    assert resp.json()["sync_status"] == "manually_edited"

    # Edit English → resync. The French manual edit must survive.
    client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "Hello {{student_name}}", "body": "Body", "template_key": "k"},
    )
    template = client.get(f"/api/courses/{course_id}/templates/k").json()
    fr = template["translations"]["fr"]
    assert fr["manual_lock"] is True
    assert fr["sync_status"] == "manually_edited"
    assert fr["subject"] == "Refined French greeting"

    # Other locale re-syncs fresh instead
    assert template["translations"]["es"]["sync_status"] == "in_sync"


def test_reset_lock_allows_resync(client, course_id):
    client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "S {{student_name}}", "body": "B", "template_key": "kk"},
    )
    client.patch(
        f"/api/courses/{course_id}/templates/kk/translations/de",
        json={"subject": "Custom German", "body": "Custom body"},
    )
    resp = client.delete(f"/api/courses/{course_id}/templates/kk/translations/de/lock")
    assert resp.status_code == 200
    assert resp.json()["sync_status"] == "in_sync"

    client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "S {{student_name}}", "body": "B", "template_key": "kk"},
    )
    template = client.get(f"/api/courses/{course_id}/templates/kk").json()
    assert template["translations"]["de"]["manual_lock"] is False


def test_404_unknown_locale(client, course_id):
    resp = client.patch(
        f"/api/courses/{course_id}/templates/k/translations/xx",
        json={"subject": "s", "body": "b"},
    )
    assert resp.status_code == 404


def test_422_unknown_target_code(client, course_id):
    resp = client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "s", "body": "b", "target_codes": ["xx"]},
    )
    assert resp.status_code == 422


def test_subset_request_keeps_other_locales_persisted(client, course_id):
    client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "s {{student_name}}", "body": "b", "template_key": "sub"},
    )
    resp = client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "s {{student_name}}", "body": "b", "template_key": "sub", "target_codes": ["th"]},
    )
    assert resp.status_code == 200
    saved = client.get(f"/api/courses/{course_id}/templates/sub").json()
    assert "es" in saved["translations"]  # untouched persisted locale kept
    assert "th" in saved["translations"]


def test_sanitization_applied_to_stored_translations(client, course_id):
    resp = client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={
            "subject": "Evil <script>alert(1)</script> Subject",
            "body": "Body with <iframe src='x'></iframe> content",
            "template_key": "sec",
        },
    )
    body = resp.json()
    assert "<script" not in body["translations"]["es"]["subject"]
    assert "<iframe" not in body["translations"]["es"]["body"]
    template = client.get(f"/api/courses/{course_id}/templates/sec").json()
    assert "script" not in template["english_subject"].lower()
    assert "iframe" not in template["english_body"].lower()


def test_export_integrity_shape(client, course_id):
    resp = client.post(
        f"/api/courses/{course_id}/templates/auto-translate",
        json={"subject": "s", "body": "b", "template_key": "export"},
    )
    body = resp.json()
    assert set(body.keys()) == {"course_id", "template_key", "translations", "audit"}
    assert body["course_id"] == course_id
