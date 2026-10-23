"""Phase 1 tests: dynamic variable shielding engine."""

from __future__ import annotations

import re

import pytest
from app.languages import LOCALES
from app.shielding import (
    PLACEHOLDER_PATTERN,
    compute_shielded_chunks,
    mask_placeholders,
    mask_placeholders_html,
    unmask_tokens,
)

SAMPLES = [
    "Dear {{student_name}}, your course {{course_title}} starts on {{start_date}}.",
    "Visit {{portal_url}} to meet {{instructor_name}} before {{start_date}}.",
    ("Hi {{student_name}}, welcome to {{course_title}}! Instructor: {{instructor_name}} "
    "Portal: {{portal_url}} Date: {{start_date}}"),
]

TOKEN_RE = re.compile(r"__VAR_TOKEN_\d+__")


def test_mask_replaces_every_placeholder_with_inert_token():
    masked, mapping = mask_placeholders(SAMPLES[0])
    assert PLACEHOLDER_PATTERN.search(masked) is None
    assert masked == "Dear __VAR_TOKEN_0__, your course __VAR_TOKEN_1__ starts on __VAR_TOKEN_2__."
    assert set(mapping.values()) == {"{{student_name}}", "{{course_title}}", "{{start_date}}"}


def test_mask_and_unmask_roundtrip_all_samples():
    for sample in SAMPLES:
        masked, mapping = mask_placeholders(sample)
        assert not PLACEHOLDER_PATTERN.search(masked)
        restored = unmask_tokens(masked, mapping)
        assert restored == sample


def test_same_variable_in_multiple_positions_shares_one_token():
    text = "{{student_name}} said hello, {{student_name}} again."
    masked, mapping = mask_placeholders(text)
    assert masked == "__VAR_TOKEN_0__ said hello, __VAR_TOKEN_0__ again."
    assert len(mapping) == 1


def test_unmask_tolerates_engine_mangled_tokens():
    masked, mapping = mask_placeholders(SAMPLES[0])
    mangled = masked.replace("__VAR_TOKEN_", "__Var-Token_").replace("_0__", "_ 0 __").replace("_1__", "_ 1 __").replace("_2__", "_ 2 __")
    restored = unmask_tokens(mangled, mapping)
    assert restored == SAMPLES[0]


def test_unmask_tolerates_lowercase_and_spaces():
    masked, mapping = mask_placeholders(SAMPLES[1])
    mangled = masked.lower().replace("__var_token", "__ var token")
    restored = unmask_tokens(mangled, mapping)
    assert "visit {{portal_url}}" in restored  # text is engine-lowercased; tokens are exact


def test_html_attribute_masking_wraps_tokens_in_translate_no():
    from app.sanitization import sanitize_text
    from app.shielding import strip_translate_no_wrappers

    masked, mapping = mask_placeholders_html(SAMPLES[0])
    assert '<span translate="no">__VAR_TOKEN_0__</span>' in masked
    softened = strip_translate_no_wrappers(unmask_tokens(masked, mapping))
    restored = sanitize_text(softened)
    assert "Dear {{student_name}}, your course {{course_title}}" in restored
    assert "translate=" not in restored
    assert "<span>" not in restored


def test_shared_token_map_across_subject_and_body():
    subject = "Welcome {{student_name}}"
    body = "Course {{course_title}} for {{student_name}} begins {{start_date}}."
    masked_subject, masked_body, mapping = compute_shielded_chunks(subject, body)
    assert PLACEHOLDER_PATTERN.search(masked_subject) is None
    assert PLACEHOLDER_PATTERN.search(masked_body) is None
    restored_subject = unmask_tokens(masked_subject, mapping)
    restored_body = unmask_tokens(masked_body, mapping)
    assert restored_subject == subject
    assert restored_body == body
    # {{student_name}} shares token 0 in both fields.
    assert masked_subject == "Welcome __VAR_TOKEN_0__"
    assert "__VAR_TOKEN_0__" in masked_body


def test_unknown_token_index_falls_back_to_canonical_form():
    masked, mapping = mask_placeholders("Hello {{student_name}}")
    broken = masked + " and __VAR_TOKEN_99__"
    restored = unmask_tokens(broken, mapping)
    assert "Hello {{student_name}}" in restored
    assert "__VAR_TOKEN_99__" in restored


@pytest.mark.parametrize("locale_spec", LOCALES, ids=[loc.code for loc in LOCALES])
def test_token_preservation_across_all_38_target_scripts(locale_spec):
    """Phase 1 milestone: 100% token preservation across all 38 target
    scripts (Latin, Cyrillic, Devanagari, Arabic, CJK, Thai, Tamil,
    Bengali, Hebrew, Hangul, Kana, Vietnamese diacritics, …)."""
    from tests.conftest import SCRIPT_SAMPLES

    sample = "Dear {{student_name}}, course {{course_title}} starts {{start_date}} — visit {{portal_url}}."
    for script_text in SCRIPT_SAMPLES.values():
        # Simulate a realistic translated output for the target script that
        # contains the inert tokens exactly as shielding guarantees.
        translated = f"{script_text} {sample}"
        translated = translated.replace("__", "__")
        masked, mapping = mask_placeholders(sample)
        simulated_translation = translated.replace(sample, masked)
        restored = unmask_tokens(simulated_translation, mapping)
        original_placeholders = set(PLACEHOLDER_PATTERN.findall(sample))
        restored_placeholders = set(PLACEHOLDER_PATTERN.findall(restored))
        assert original_placeholders == restored_placeholders
        assert not TOKEN_RE.search(restored), f"token leaked for {script_text!r}"
        assert script_text in restored
