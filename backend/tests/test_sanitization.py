"""Sanitization tests: HTML/script stripper (plan §5)."""

from __future__ import annotations

from app.sanitization import sanitize_text


def test_strips_script_tags():
    text = "Hello <script>alert('xss')</script> world"
    assert sanitize_text(text) == "Hello  world"


def test_strips_event_handlers():
    text = "Hello <b onmouseover=alert(1)>bold</b> text"
    out = sanitize_text(text)
    assert "onmouseover" not in out
    assert "<b>bold</b>" in out


def test_strips_javascript_urls():
    text = 'Click <a href="javascript:alert(1)">here</a> for details'
    out = sanitize_text(text)
    assert "javascript:" not in out


def test_keeps_safe_https_anchor():
    text = 'See <a href="https://portal.example.com/login">portal</a>'
    out = sanitize_text(text)
    assert '<a href="https://portal.example.com/login">portal</a>' in out


def test_strips_iframe_style_object():
    for tag in ("iframe", "style", "object", "embed", "form", "meta", "link"):
        text = f"Start <{tag} src='x'></{tag}> End"
        assert sanitize_text(text) == "Start  End"


def test_escapes_unknown_tags():
    text = "Text with <weirdtag>stray</weirdtag> end"
    assert sanitize_text(text) == "Text with &lt;weirdtag&gt;stray&lt;/weirdtag&gt; end"


def test_plain_text_untouched():
    text = "Plain translated text with {{student_name}} {{course_title}} — naïve café."
    assert sanitize_text(text) == text


def test_drops_attributes_from_allowed_tags():
    out = sanitize_text('<span translate="no">{{student_name}}</span>')
    assert "<script" not in out and "translate=" not in out
    assert "{{student_name}}" in out
