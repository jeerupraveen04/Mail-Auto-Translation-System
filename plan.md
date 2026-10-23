# Implementation Plan: Multilingual Course Mail Auto-Translation System

## Project Overview

This engineering plan details the architecture, roadmap, and technical milestones for integrating an enterprise-grade automated translation pipeline into a **Course Management System (LMS)** supporting **38 global languages**. 

Whenever an administrator or instructor inputs course email notifications or descriptions in English, the system automatically translates and populates all 38 target languages in real time using a 3-tier waterfall cascade: **DeepL ➔ AWS Translate ➔ Google Gemini**, while strictly safeguarding dynamic placeholders (e.g., `{{student_name}}`, `{{course_title}}`).

---

## 1. System Architecture & End-to-End Data Flow

```
[Administrator Types English Mail Description & Subject]
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 1. Client-Side Input & Debouncing (1,000ms delay)      │
│    - Detects typing pause or explicit "Translate All"  │
│    - Scans for tokens: {{student_name}}, {{url}}, etc. │
└────────────────────────┬───────────────────────────────┘
                         │ REST / WebSocket
                         ▼
┌────────────────────────────────────────────────────────┐
│ 2. Variable Shielding & Token Masking                  │
│    - Masks {{var}} into inert tokens (__VAR_TOKEN_N__) │
│    - Computes SHA-256 hash of text for cache lookup    │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 3. Cache Verification Layer (Redis)                    │
│    - Cache Hit: Returns pre-translated 38-lang payload │
│    - Cache Miss: Dispatches parallel worker jobs       │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 4. Parallel Cascade Translation Engine (38 Languages)  │
│                                                        │
│   Tier 1: DeepL API (Pro / Free)                      │
│     ├── Evaluates 30+ European & CJK locales           │
│     └── On success: Resolves with engine tag 'DeepL'   │
│                                                        │
│   Tier 2: AWS Translate (Failover & Indic / SE Asian)  │
│     ├── Triggered for Hindi, Bengali, Tamil, Thai, etc.│
│     └── Triggered if DeepL returns 429 or timeout      │
│                                                        │
│   Tier 3: Google Gemini (Gemini 1.5 Flash / Pro)       │
│     ├── Triggered if AWS Translate fails or errors     │
│     └── LLM context reasoning with zero token drift    │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 5. Token Unmasking & RTL Formatting                    │
│    - Restores __VAR_TOKEN_N__ back to {{var}}          │
│    - Flags RTL languages (Arabic 'ar', Hebrew 'he')    │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 6. Database Persistence & UI State Update              │
│    - Saves to PostgreSQL JSONB column                  │
│    - Populates 38 form fields in LMS UI with badges    │
└────────────────────────────────────────────────────────┘
```

---

## 2. Phased Implementation Roadmap

### Phase 1: Dynamic Variable Shielding Engine
- **Objective**: Prevent translation engines from corrupting dynamic template variables (e.g., turning `{{student_name}}` into `{{nombre_estudiante}}` in Spanish or `{{विद्यार्थी_का_नाम}}` in Hindi).
- **Milestones**:
  - Implement bidirectional regex tokenizer:
    - Pre-processing: `/\{\{([a-zA-Z0-9_]+)\}\}/g` ➔ `__VAR_TOKEN_0__`
    - Post-processing: `__VAR_TOKEN_0__` ➔ `{{student_name}}`
  - Support HTML attribute masking (`<span translate="no">{{student_name}}</span>`) for services supporting MIME type `text/html`.
  - Build automated unit tests validating 100% token preservation across all 38 target scripts (Latin, Cyrillic, Devanagari, Arabic, CJK, Thai, Tamil, Bengali).

### Phase 2: Translation Engine Adapters & Credentials Setup
- **Objective**: Standardize client adapters for DeepL, AWS Translate, and Google Gemini with shared interfaces.
- **Milestones**:
  - **DeepL Adapter**:
    - Setup `deepl-python` client with target code mappings (e.g., `zh-CN` ➔ `ZH-HANS`, `zh-TW` ➔ `ZH-HANT`, `no` ➔ `NB`, `pt` ➔ `PT-PT`).
    - Configure concurrency limit and connection pooling.
  - **AWS Translate Adapter**:
    - Setup AWS SDK (`boto3` / `@aws-sdk/client-translate`) with retry policies and exponential backoff.
    - Map BCP 47 locales to AWS supported codes.
  - **Google Gemini Adapter**:
    - Setup Google GenAI SDK (`google-genai` / `@google/generative-ai`) targeting `gemini-1.5-flash` for high-throughput, low-latency translation.
    - System prompt: *"Translate the educational notification into {target_language}. Maintain exact casing for code variables matching __VAR_TOKEN_X__."*

### Phase 3: Cascading Waterfall & Circuit Breaker Logic
- **Objective**: Orchestrate seamless fallback across providers without service disruption.
- **Milestones**:
  - Implement waterfall decision tree:
    1. If `target_code` in `DEEPL_SUPPORTED` and DeepL credentials valid ➔ Call DeepL.
    2. If DeepL call fails (HTTP 429, 500, timeout > 3000ms) or language unsupported ➔ Cascade to AWS Translate.
    3. If AWS Translate call fails ➔ Cascade to Google Gemini.
    4. If Gemini call fails ➔ Return error status with manual review flag.
  - Audit logging: Tag each translated language with metadata (`resolved_engine`, `latency_ms`, `timestamp`).

### Phase 4: LMS Backend Architecture & Queue Management
- **Objective**: Handle concurrent translation for 38 languages without blocking the web application thread.
- **Milestones**:
  - Implement background worker pool (Python `ThreadPoolExecutor` or Celery / BullMQ in Node.js).
  - Expose REST API endpoint:
    - `POST /api/courses/{course_id}/templates/auto-translate`
    - Request: `{ "subject": "...", "body": "...", "target_codes": [...] }`
    - Response: `{ "translations": { "es": { "subject": "...", "body": "...", "engine": "DeepL" }, ... } }`
  - Implement Redis caching keyed by `SHA-256(source_text) + target_code` with 30-day TTL to save API costs on repetitive template updates.

### Phase 5: Database Schema & Storage
- **Objective**: Store translations in an indexed, query-optimized format.
- **Milestones**:
  - PostgreSQL Table Migration:
    ```sql
    CREATE TABLE course_mail_templates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        course_id VARCHAR(64) NOT NULL,
        template_key VARCHAR(64) NOT NULL,
        source_lang VARCHAR(8) DEFAULT 'en',
        english_subject TEXT NOT NULL,
        english_body TEXT NOT NULL,
        pipeline VARCHAR(64) DEFAULT 'DeepL > AWS > Gemini',
        translations JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );

    CREATE INDEX idx_mail_templates_lookup 
    ON course_mail_templates(course_id, template_key);

    CREATE INDEX idx_mail_templates_gin 
    ON course_mail_templates USING gin(translations);
    ```

### Phase 6: Frontend LMS Studio & User Experience
- **Objective**: Provide course administrators with a frictionless multilingual editing interface.
- **Milestones**:
  - **Dynamic Input**: Debounced input (1,000–1,200ms) triggers auto-sync as the admin writes English content.
  - **Placeholder Chips Toolbar**: Quick-insert buttons for `{{student_name}}`, `{{course_title}}`, `{{start_date}}`, `{{portal_url}}`, `{{instructor_name}}`.
  - **38 Language Grid / Cards**:
    - Engine badge indicating which tier translated the text (DeepL: Cyan, AWS: Amber, Gemini: Purple).
    - Sync status indicator (`In Sync` vs. `Manually Edited`).
    - Automatic `dir="rtl"` and alignment styling for Arabic (`ar`) and Hebrew (`he`).
  - **Student Email Simulator**: Modal that injects realistic sample data into placeholders to preview how emails look to real students before sending.
  - **Manual Override Management**: Allows instructors to refine specific translations; custom edits are locked and never overwritten by subsequent auto-syncs unless manually reset.

---

## 3. Language Routing Matrix (38 Target Locales)

| # | Locale Code | Language Name | Script Direction | Primary Engine | Fallback 1 | Fallback 2 |
|---|-------------|---------------|------------------|----------------|------------|------------|
| 1 | `es` | Spanish | LTR | **DeepL** | AWS Translate | Gemini |
| 2 | `fr` | French | LTR | **DeepL** | AWS Translate | Gemini |
| 3 | `de` | German | LTR | **DeepL** | AWS Translate | Gemini |
| 4 | `it` | Italian | LTR | **DeepL** | AWS Translate | Gemini |
| 5 | `pt` | Portuguese | LTR | **DeepL** (`PT-PT`) | AWS Translate | Gemini |
| 6 | `ru` | Russian | LTR | **DeepL** | AWS Translate | Gemini |
| 7 | `zh-CN` | Chinese (Simplified) | LTR | **DeepL** (`ZH-HANS`) | AWS Translate | Gemini |
| 8 | `zh-TW` | Chinese (Traditional) | LTR | **DeepL** (`ZH-HANT`) | AWS Translate | Gemini |
| 9 | `ja` | Japanese | LTR | **DeepL** | AWS Translate | Gemini |
| 10 | `ko` | Korean | LTR | **DeepL** | AWS Translate | Gemini |
| 11 | `hi` | Hindi | LTR | **AWS Translate** | Gemini | DeepL |
| 12 | `ar` | Arabic | **RTL** | **DeepL** | AWS Translate | Gemini |
| 13 | `tr` | Turkish | LTR | **DeepL** | AWS Translate | Gemini |
| 14 | `nl` | Dutch | LTR | **DeepL** | AWS Translate | Gemini |
| 15 | `pl` | Polish | LTR | **DeepL** | AWS Translate | Gemini |
| 16 | `id` | Indonesian | LTR | **DeepL** | AWS Translate | Gemini |
| 17 | `vi` | Vietnamese | LTR | **AWS Translate** | Gemini | DeepL |
| 18 | `th` | Thai | LTR | **AWS Translate** | Gemini | DeepL |
| 19 | `uk` | Ukrainian | LTR | **DeepL** | AWS Translate | Gemini |
| 20 | `sv` | Swedish | LTR | **DeepL** | AWS Translate | Gemini |
| 21 | `ro` | Romanian | LTR | **DeepL** | AWS Translate | Gemini |
| 22 | `cs` | Czech | LTR | **DeepL** | AWS Translate | Gemini |
| 23 | `el` | Greek | LTR | **DeepL** | AWS Translate | Gemini |
| 24 | `hu` | Hungarian | LTR | **DeepL** | AWS Translate | Gemini |
| 25 | `da` | Danish | LTR | **DeepL** | AWS Translate | Gemini |
| 26 | `fi` | Finnish | LTR | **DeepL** | AWS Translate | Gemini |
| 27 | `no` | Norwegian | LTR | **DeepL** (`NB`) | AWS Translate | Gemini |
| 28 | `sk` | Slovak | LTR | **DeepL** | AWS Translate | Gemini |
| 29 | `bg` | Bulgarian | LTR | **DeepL** | AWS Translate | Gemini |
| 30 | `hr` | Croatian | LTR | **DeepL** | AWS Translate | Gemini |
| 31 | `lt` | Lithuanian | LTR | **DeepL** | AWS Translate | Gemini |
| 32 | `sl` | Slovenian | LTR | **DeepL** | AWS Translate | Gemini |
| 33 | `lv` | Latvian | LTR | **DeepL** | AWS Translate | Gemini |
| 34 | `et` | Estonian | LTR | **DeepL** | AWS Translate | Gemini |
| 35 | `he` | Hebrew | **RTL** | **AWS Translate** | Gemini | DeepL |
| 36 | `ms` | Malay | LTR | **AWS Translate** | Gemini | DeepL |
| 37 | `bn` | Bengali | LTR | **AWS Translate** | Gemini | DeepL |
| 38 | `ta` | Tamil | LTR | **AWS Translate** | Gemini | DeepL |

---

## 4. Cost Optimization & Performance Strategy

1. **Parallel Execution Time**:
   - Sequential execution across 38 languages would take 25–40 seconds.
   - Using a worker pool (`ThreadPoolExecutor(max_workers=10)`), all 38 requests execute concurrently within **1.2 to 2.5 seconds**.
2. **Cost Breakdown & Savings via Caching**:
   - DeepL API: ~$20 per 1M characters.
   - AWS Translate: ~$15 per 1M characters.
   - Gemini 1.5 Flash: ~$0.075 per 1M input tokens.
   - An average course email is ~500 characters. Translating to 38 languages costs less than **$0.0004 per email campaign**.
   - Redis hashing prevents re-billing when instructors make cosmetic edits or repeatedly review identical drafts.
3. **Rate Limit Management**:
   - DeepL: Defaults to 50 concurrent connections (well within our 10-worker pool).
   - AWS Translate: 10 transactions per second (TPS) soft limit per region; exponential backoff automatically absorbs spikes.

---

## 5. Security & Data Protection

- **API Secrets**: Credentials (`DEEPL_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `GEMINI_API_KEY`) must reside in an encrypted vault (AWS Secrets Manager, Doppler, or HashiCorp Vault) rather than hardcoded in source files.
- **Zero Student PII Leaks**: Placeholders are sent as literal tokens (`__VAR_TOKEN_0__`) during translation. Student personal data (real student names, personal emails, student IDs) is **never** sent to third-party translation APIs. Real data is only injected locally at final email delivery time.
- **Sanitization**: All translated strings are sanitized with an HTML/script stripper before storage to prevent Cross-Site Scripting (XSS).

---

## 6. Verification & Quality Assurance Checklist

- [ ] **Variable Shielding Verification**: Ensure `{{student_name}}` and `{{course_title}}` remain identical across all 38 target language outputs.
- [ ] **Failover Testing**: Simulate DeepL 429 response and verify seamless fallback to AWS Translate.
- [ ] **RTL Rendering**: Verify Arabic and Hebrew properly display with right-to-left text alignment in the LMS frontend.
- [ ] **Manual Edit Lock**: Confirm that manual edits made by an instructor to French or German are not wiped out when English text is edited.
- [ ] **Export Integrity**: Verify that exported JSON payload adheres strictly to database schema standards.
