# Multilingual Course Mail Auto-Translation System

Auto-populates all **38 target languages** for course mail templates using a
3-tier waterfall cascade: **DeepL ➔ AWS Translate ➔ Google Gemini**, with
strict protection for dynamic placeholders (`{{student_name}}`, …). Full
requirements live in `plan.md`.

## Quick start

```bash
./run.sh                      # serves API + LMS Studio on :8000
# or manually:
.venv/bin/uvicorn app.main:app --port 8000 --app-dir backend
```

Infrastructure (PostgreSQL + Redis) defaults are configured for the local
docker containers (`localhost:55432` / `redis://localhost:6379/0`) — see
`.env.example` and the environment table below.

Configure at least one provider key for translations to resolve; without any
keys the pipeline gracefully marks every locale `manual_review` with the
failure audit chain intact (failover emulation works in tests).

## Architecture

```
backend/
  app/
    languages.py        # 38-locale routing matrix (plan §3), RTL flags, cascades
    shielding.py        # Phase 1: {{var}} ⇄ __VAR_TOKEN_N__ + <span translate="no">
    sanitization.py     # HTML/script stripper applied before storage (plan §5)
    adapters/           # Phase 2: deepl_engine.py | aws_engine.py | gemini_engine.py
    pipeline.py         # Phase 3: waterfall + attempt audit metadata
    circuit_breaker.py  # per-engine open/half-open/close breaker
    worker.py           # Phase 4: ThreadPoolExecutor(max_workers=10)
    cache.py            # Redis: SHA-256(subject+body):locale, 30-day TTL
    db.py               # Phase 5: JSONB schema, indexes, manual-lock persistence
    schemas.py, routes.py, main.py   # REST surface + app wiring
  migrations/           # raw SQL migration (matches plan §5 exactly)
  tests/                # 94 tests incl. QA checklist scenarios
frontend/
  index.html, app.js, styles.css    # Phase 6: LMS Studio
```

## REST API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/courses/{course_id}/templates/auto-translate` | mask → cache-check → parallel cascade → unmask → sanitize → persist |
| GET | `/api/courses/{course_id}/templates/{template_key}` | fetch saved template + translations |
| PATCH | `/api/courses/{course_id}/templates/{template_key}/translations/{locale}` | manual override (locks the locale) |
| DELETE | `/api/courses/{course_id}/templates/{template_key}/translations/{locale}/lock` | remove manual lock |
| GET | `/api/languages` | 38-locale matrix for the UI |
| GET | `/api/health` | engine config, circuit states, cache + DB status |

Request/response:

```json
POST /api/courses/CRS-1/templates/auto-translate
{ "subject": "Welcome {{student_name}}!", "body": "…", "template_key": "welcome" }

→ { "translations": { "es": { "subject": "…", "body": "…", "engine": "DeepL",
      "tier": 1, "latency_ms": 240, "manual_review": false, "rtl": false }, … },
    "audit": [ { "locale": "es", "resolved_engine": "DeepL", "cached": false,
      "latency_ms": 240, "timestamp": 1761234567.8, "digest": "sha256…" }, … ] }
```

## LMS Studio features (Phase 6)

- Debounced (1,000 ms) auto-sync while typing English subject/body; explicit
  **Translate All** forces a cache-bypass pass.
- Placeholder chips toolbar for the 5 supported variables.
- 38 language cards with `dir="rtl"` styling for Arabic/Hebrew, engine badges
  (DeepL cyan · AWS amber · Gemini purple), cached + in-sync status chips.
- **Student Email Simulator** modal — injects sample data locally; real PII
  never leaves the deploy (only inert `__VAR_TOKEN_N__` tokens are translated).
- Manual override: editing any locale locks it (`🔒 Manual`); locked locales
  are untouched by later auto-syncs until the lock is released.

## Tests

```bash
.venv/bin/python -m pytest backend/tests -q    # 94 passed
```

Covers: shielding round-trips + 38-script token preservation, XSS stripping,
waterfall failover (429 → AWS → Gemini), circuit breaker states, Redis cache
round-trips, PostgreSQL schema + GIN/lookup indexes, manual-edit lock
survival, REST contract, export integrity.

## Environment

| Variable | Default | Notes |
|----------|---------|-------|
| `DEEPL_API_KEY` | – | Tier 1 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | – | Tier 2 |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-1.5-flash` | Tier 3 |
| `DATABASE_URL` | docker local postgres | SQL persistence |
| `REDIS_URL` | `redis://localhost:6379/0` | cache; degrades to no-op if down |
| `WORKER_POOL_SIZE` | `10` | parallel 38-language fanout |
| `CACHE_TTL_SECONDS` | `2592000` (30 days) | |

Secrets belong in a vault (AWS Secrets Manager / Doppler / Vault) and are
only referenced via environment — never committed (plan §5).
