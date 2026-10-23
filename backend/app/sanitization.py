"""Sanitization: HTML/script stripper applied to every translated string
before storage (plan.md §5) to prevent stored XSS in the LMS."""

from __future__ import annotations

import html
import re

_BLOCKED_TAGS = (
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "applet",
    "form",
    "link",
    "meta",
    "base",
    "frame",
    "frameset",
    "svg",
    "math",
)
_BLOCK_ANY = re.compile(r"<\s*({})\b[^>]*>.*?<\s*/\s*\1\s*>".format("|".join(_BLOCKED_TAGS)), re.IGNORECASE | re.DOTALL)
_BLOCK_SELFCLOSING = re.compile(r"<\s*/?\s*({})\b[^>]*>".format("|".join(_BLOCKED_TAGS)), re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EVENT_ATTR = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_DANGEROUS_URL = re.compile(
    r"(href|src|action)\s*=\s*(\"|')?\s*(javascript|vbscript|data)[^\s\"'>]*(\"|')?", re.IGNORECASE
)

_ALLOWED_TAG_NAMES = {"b", "strong", "i", "em", "u", "br", "p", "span", "a"}
_ANY_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>")
_ANCHOR_TAG = re.compile(r"<\s*a\b([^>]*)>(.*?)<\s*/\s*a\s*>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"href\s*=\s*['\"]([^'\"]*)['\"]", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"style\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)


def sanitize_text(text: str) -> str:
    """Strip scripts, event handlers, dangerous URLs and non-whitelisted
    tags from a translated string. Allowed formatting tags are kept but
    always attribute-stripped; anchors are rebuilt with safe hrefs only."""
    if not text:
        return text

    cleaned = _BLOCK_ANY.sub("", text)
    cleaned = _BLOCK_SELFCLOSING.sub("", cleaned)
    cleaned = _COMMENT.sub("", cleaned)
    cleaned = _DANGEROUS_URL.sub("", cleaned)
    cleaned = _EVENT_ATTR.sub("", cleaned)

    # Allowlist pass: keep simple formatting tags but strip ALL their
    # attributes (safe after event-handler/URL cleanup); escape anything else.
    def _clean_tag(match: re.Match[str]) -> str:
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if name == "a" and not closing:
            return match.group(0)  # passed to the anchor rebuild pass below
        if name not in _ALLOWED_TAG_NAMES:
            return html.escape(match.group(0))
        if attrs.strip():
            return f"<{name}>" if not closing else f"</{name}>"
        return f"</{name}>" if closing else match.group(0).lower()

    cleaned = _ANY_TAG.sub(_clean_tag, cleaned)

    # Rebuild <a> tags: only safe http(s)/mailto hrefs survive.
    def _rebuild_a(match: re.Match[str]) -> str:
        href_match = _HREF_RE.search(match.group(1))
        inner = match.group(2)
        if not href_match:
            return ""
        href = html.unescape(href_match.group(1)).strip()
        if href.lower().startswith(("http://", "https://", "mailto:")):
            return f'<a href="{html.escape(href, quote=True)}">{inner}</a>'
        return inner

    cleaned = _ANCHOR_TAG.sub(_rebuild_a, cleaned)
    return cleaned.strip()
