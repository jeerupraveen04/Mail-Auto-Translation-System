"""Language routing matrix for the 38 target locales (plan.md section 3).

Each locale declares:
- code (BCP 47-lite locale code used across the system)
- name (human readable)
- script direction (RTL flags for `ar`, `he`)
- engine cascade order (primary + fallbacks), matching the plan's
  routing matrix: DeepL-primary locales cascade [DeepL, AWS, Gemini];
  AWS-primary locales (Indic / SE Asian / RTL-AWS) cascade [AWS, Gemini, DeepL].
"""

from __future__ import annotations

from dataclasses import dataclass

ENGINE_TIER_1 = "DeepL"
ENGINE_TIER_2 = "AWS"
ENGINE_TIER_3 = "Gemini"

DEEPL_CASCADE = (ENGINE_TIER_1, ENGINE_TIER_2, ENGINE_TIER_3)
AWS_CASCADE = (ENGINE_TIER_2, ENGINE_TIER_3, ENGINE_TIER_1)


@dataclass(frozen=True)
class LocaleSpec:
    code: str
    name: str
    rtl: bool
    cascade: tuple[str, ...]


LOCALES: tuple[LocaleSpec, ...] = (
    LocaleSpec("es", "Spanish", False, DEEPL_CASCADE),
    LocaleSpec("fr", "French", False, DEEPL_CASCADE),
    LocaleSpec("de", "German", False, DEEPL_CASCADE),
    LocaleSpec("it", "Italian", False, DEEPL_CASCADE),
    LocaleSpec("pt", "Portuguese", False, DEEPL_CASCADE),
    LocaleSpec("ru", "Russian", False, DEEPL_CASCADE),
    LocaleSpec("zh-CN", "Chinese (Simplified)", False, DEEPL_CASCADE),
    LocaleSpec("zh-TW", "Chinese (Traditional)", False, DEEPL_CASCADE),
    LocaleSpec("ja", "Japanese", False, DEEPL_CASCADE),
    LocaleSpec("ko", "Korean", False, DEEPL_CASCADE),
    LocaleSpec("hi", "Hindi", False, AWS_CASCADE),
    LocaleSpec("ar", "Arabic", True, DEEPL_CASCADE),
    LocaleSpec("tr", "Turkish", False, DEEPL_CASCADE),
    LocaleSpec("nl", "Dutch", False, DEEPL_CASCADE),
    LocaleSpec("pl", "Polish", False, DEEPL_CASCADE),
    LocaleSpec("id", "Indonesian", False, DEEPL_CASCADE),
    LocaleSpec("vi", "Vietnamese", False, AWS_CASCADE),
    LocaleSpec("th", "Thai", False, AWS_CASCADE),
    LocaleSpec("uk", "Ukrainian", False, DEEPL_CASCADE),
    LocaleSpec("sv", "Swedish", False, DEEPL_CASCADE),
    LocaleSpec("ro", "Romanian", False, DEEPL_CASCADE),
    LocaleSpec("cs", "Czech", False, DEEPL_CASCADE),
    LocaleSpec("el", "Greek", False, DEEPL_CASCADE),
    LocaleSpec("hu", "Hungarian", False, DEEPL_CASCADE),
    LocaleSpec("da", "Danish", False, DEEPL_CASCADE),
    LocaleSpec("fi", "Finnish", False, DEEPL_CASCADE),
    LocaleSpec("no", "Norwegian", False, DEEPL_CASCADE),
    LocaleSpec("sk", "Slovak", False, DEEPL_CASCADE),
    LocaleSpec("bg", "Bulgarian", False, DEEPL_CASCADE),
    LocaleSpec("hr", "Croatian", False, DEEPL_CASCADE),
    LocaleSpec("lt", "Lithuanian", False, DEEPL_CASCADE),
    LocaleSpec("sl", "Slovenian", False, DEEPL_CASCADE),
    LocaleSpec("lv", "Latvian", False, DEEPL_CASCADE),
    LocaleSpec("et", "Estonian", False, DEEPL_CASCADE),
    LocaleSpec("he", "Hebrew", True, AWS_CASCADE),
    LocaleSpec("ms", "Malay", False, AWS_CASCADE),
    LocaleSpec("bn", "Bengali", False, AWS_CASCADE),
    LocaleSpec("ta", "Tamil", False, AWS_CASCADE),
)

LOCALE_MAP: dict[str, LocaleSpec] = {loc.code: loc for loc in LOCALES}
DEFAULT_TARGET_CODES: tuple[str, ...] = tuple(loc.code for loc in LOCALES)
RTL_LOCALES: frozenset[str] = frozenset(loc.code for loc in LOCALES if loc.rtl)

# Placeholder chips exposed in the LMS Studio toolbar (plan Phase 6).
PLACEHOLDER_CHIPS: tuple[str, ...] = (
    "student_name",
    "course_title",
    "start_date",
    "portal_url",
    "instructor_name",
)


def locale(code: str) -> LocaleSpec:
    try:
        return LOCALE_MAP[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported target locale: {code!r}") from exc


def engine_cascade(code: str) -> tuple[str, ...]:
    return locale(code).cascade
