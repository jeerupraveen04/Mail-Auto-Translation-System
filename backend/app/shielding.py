"""Phase 1: Dynamic Variable Shielding Engine.

Bidirectional regex tokenizer that protects dynamic template variables
(``{{student_name}}`` …) from being corrupted by translation engines.

- Pre-processing: ``{{var}}`` -> ``__VAR_TOKEN_0__``
- Post-processing: ``__VAR_TOKEN_0__`` -> ``{{var}}``
- HTML attribute masking: wraps tokens in ``<span translate="no">`` for
  engines invoked with MIME type ``text/html``.

The unmasking step is deliberately tolerant: engines occasionally mangle
the token casing/whitespace (e.g. ``__ Var_Token_0__``); all variants are
restored to the exact original ``{{var}}`` form.
"""

from __future__ import annotations

import re

# Matches a dynamic template variable such as {{student_name}}.
PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

# Standard inert token form produced during masking (tolerates casing /
# whitespace drift introduced by translation layers).
TOKEN_TEMPLATE = "__VAR_TOKEN_{n}__"
TOKEN_PATTERN = re.compile(r"__\s*[{_<\[]?\s*VAR[\s_.-]*TOKEN[\s_.-]*?(\d+)\s*[\]>_}]*\s*__", re.IGNORECASE)
# Plainword form, in case an engine drops the surrounding underscores.
TOKEN_PLAIN_PATTERN = re.compile(r"\bVAR[\s_.-]*TOKEN[\s_.-]*?(\d+)\b", re.IGNORECASE)
# HTML masking scaffolding produced by mask_placeholders_html.
SPAN_WRAPPER = re.compile(
    r"<\s*span[^>]*translate\s*=\s*['\"]no['\"][^>]*>(.*?)<\s*/\s*span\s*>", re.IGNORECASE | re.DOTALL
)


def strip_translate_no_wrappers(text: str) -> str:
    """Remove HTML masking scaffolding from an HTML-mode translation."""
    return SPAN_WRAPPER.sub(r"\1", text)


def mask_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Replace every ``{{var}}`` with an inert ``__VAR_TOKEN_N__`` token.

    Returns the masked text plus an ordered mapping
    ``{"__VAR_TOKEN_0__": "{{student_name}}", ...}`` used to restore
    placeholders after translation. Multiple occurrences of the same
    variable share a single token.
    """
    mapping: dict[str, str] = {}
    next_index = len(mapping)

    def _replace(match: re.Match[str]) -> str:
        nonlocal next_index
        variable = match.group(1)
        existing = next((tok for tok, orig in mapping.items() if orig == f"{{{{{variable}}}}}"), None)
        if existing is not None:
            return existing
        token = TOKEN_TEMPLATE.format(n=next_index)
        mapping[token] = f"{{{{{variable}}}}}"
        next_index += 1
        return token

    return PLACEHOLDER_PATTERN.sub(_replace, text), mapping


def unmask_tokens(text: str, mapping: dict[str, str]) -> str:
    """Restore every ``__VAR_TOKEN_N__`` back to its exact ``{{var}}`` form.

    Uses a ladder of progressively more tolerant patterns so that even
    engine-mangled tokens (``__Var-Token 0__``, ``VAR_TOKEN_0``, …) are
    restored. Unknown indices fall back to the canonical token form.
    """
    if not mapping:
        return text

    by_index = {extract_token_index(tok): orig for tok, orig in mapping.items()}

    def _sub(match: re.Match[str], pattern_index: int) -> str:
        index = match.group(1)
        original = by_index.get(index)
        if original is not None:
            return original
        # Keep the canonical inert form if we truly cannot resolve it.
        return TOKEN_TEMPLATE.format(n=index)

    result = TOKEN_PATTERN.sub(lambda m: _sub(m, 0), text)
    result = TOKEN_PLAIN_PATTERN.sub(lambda m: _sub(m, 1), result)
    return result


def mask_placeholders_html(text: str) -> tuple[str, dict[str, str]]:
    """HTML attribute masking for engines that accept MIME type ``text/html``.

    Tokens are additionally wrapped in ``<span translate="no">`` so that
    HTML-aware translators leave them untouched. Decomposed with
    ``unmask_tokens`` (the wrapper span is stripped by the sanitizer).
    """
    masked, mapping = mask_placeholders(text)
    wrapped = re.sub(r"__VAR_TOKEN_\d+__", lambda m: f'<span translate="no">{m.group(0)}</span>', masked)
    return wrapped, mapping


def extract_token_index(token: str) -> str:
    match = re.search(r"(\d+)", token)
    if not match:
        raise ValueError(f"Malformed shielding token: {token!r}")
    return match.group(1)


def compute_shielded_chunks(subject: str, body: str, html_mode: bool = False) -> tuple[str, str, dict[str, str]]:
    """Mask both subject and body with a *shared* token mapping so a
    placeholder keeps a single invariant token across both fields."""
    mapping: dict[str, str] = {}
    masked_subject = _mask_shared(subject, mapping, html_mode)
    masked_body = _mask_shared(body, mapping, html_mode)
    return masked_subject, masked_body, mapping


def _mask_shared(text: str, mapping: dict[str, str], html_mode: bool) -> str:
    next_index = len(mapping)

    def _replace(match: re.Match[str]) -> str:
        nonlocal next_index
        variable = match.group(1)
        original = f"{{{{{variable}}}}}"
        existing = next((tok for tok, orig in mapping.items() if orig == original), None)
        if existing is not None:
            resolved = existing
        else:
            resolved = TOKEN_TEMPLATE.format(n=next_index)
            mapping[resolved] = original
            next_index += 1
        if html_mode:
            return f'<span translate="no">{resolved}</span>'
        return resolved

    return PLACEHOLDER_PATTERN.sub(_replace, text)
