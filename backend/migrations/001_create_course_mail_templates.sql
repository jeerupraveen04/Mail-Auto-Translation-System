-- Phase 5: PostgreSQL schema for course mail templates (plan §5).

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
