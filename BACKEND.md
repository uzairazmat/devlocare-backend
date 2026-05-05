# DevloCare — Backend Reference

> Comprehensive technical reference for the FastAPI service that powers the
> AI-Powered Smart Health Assistant (FYP, production-grade). Read this once
> and you should be able to operate, extend, and debug the entire backend
> without spelunking the code.
>
> Companion docs: **`CLAUDE.md`** (project memory) · **`DESIGN.md`**
> (frontend architecture) · **`CHANGELOG.md`** (release history).

---

## 1. What this is

A Python 3.11+ FastAPI application that:

- Accepts free-text symptom descriptions and returns the **Top-3 likely
  conditions** with confidence + triage + specialist + bilingual care tips +
  red flags + a SHAP/LIME-style explanation.
- Persists every (non-guest) consultation, lets users review/export/delete
  their history.
- Exposes a separate admin panel for KB CRUD, consultation review, feedback
  monitoring, and (super-admin only) user role management.
- Enforces JWT auth, role-based access, rate limiting, PII rejection, and a
  language-localised disclaimer on every prediction.

Frontend lives at `../devlocare-frontend/` (or wherever you mount it). The
two are decoupled — only the URL `VITE_API_BASE_URL` ties them together.

---

## 2. Stack & rationale

| Concern | Choice | Why |
|---|---|---|
| Framework | **FastAPI** | Async, OpenAPI for free, Pydantic v2 validation built in |
| ASGI server | **uvicorn[standard]** | `--reload` in dev, `httptools`/`uvloop` in prod |
| ORM | **SQLAlchemy 2.0 async** | First-class async, single tool for SQLite + Postgres |
| DB drivers | **aiosqlite** (dev) · **asyncpg** (prod) | Same SQLAlchemy code, zero config switch via `DATABASE_URL` |
| Schemas | **Pydantic v2** + **pydantic-settings** | Strict types, env-driven config |
| Auth | **PyJWT** + **bcrypt** | HS256 access tokens, salted password hashes |
| Rate limit | **slowapi** | FastAPI-native limiter; FRD-mandated tool |
| ML / NLP | scikit-learn, NLTK, spaCy, joblib | Baseline classifiers + entity patterns |
| Explainability | **SHAP** | UC-04 feature-importance plot |
| PDF | **reportlab** | UC-06 history export |
| CORS | `fastapi.middleware.cors` | SPA can call from any configured origin |
| Logging | stdlib `logging` w/ structured prefixes | `AUDIT …` lines for sensitive ops |

---

## 3. Repository layout

```
devlocare-backend-feat-admin-panel-backend/
├── app/
│   ├── main.py                       ← FastAPI app, lifespan, middleware
│   ├── api/v1/
│   │   ├── api_router.py             ← v1 prefix + sub-router includes
│   │   ├── deps.py                   ← get_current_user / *_optional
│   │   ├── admin_deps.py             ← require_admin / require_super_admin
│   │   └── endpoints/
│   │       ├── health.py             ← GET /health
│   │       ├── auth.py               ← /auth/{register,login,token,me}
│   │       ├── predict.py            ← POST /predict/text
│   │       ├── history.py            ← GET/DELETE/POST /history{,/:id,/export}
│   │       ├── feedback.py           ← POST /feedback , GET /feedback/monitor
│   │       └── admin/
│   │           ├── __init__.py       ← /admin sub-router
│   │           ├── dashboard.py
│   │           ├── consultations.py
│   │           ├── feedback.py
│   │           ├── knowledge_base.py
│   │           └── users.py
│   ├── core/
│   │   ├── config.py                 ← Pydantic Settings (env-driven)
│   │   ├── security.py               ← JWT encode/decode, bcrypt verify
│   │   ├── exceptions.py             ← ApiError handlers + custom errors
│   │   ├── logging.py                ← `get_logger`
│   │   └── rate_limit.py             ← slowapi Limiter + 429 handler
│   ├── db/
│   │   ├── session.py                ← async engine, init_db, get_db
│   │   ├── models.py                 ← SQLAlchemy ORM tables
│   │   └── crud.py                   ← (legacy) shared SQL helpers
│   ├── models/                       ← Pydantic schemas (NOT ORM)
│   │   ├── request.py                ← request bodies + PII regex guard
│   │   ├── response.py               ← user / prediction / history / feedback
│   │   ├── admin.py                  ← admin-panel payloads
│   │   └── user.py                   ← internal user helpers
│   ├── repositories/                 ← thin DB-access layer (queries only)
│   │   ├── log_repo.py               ← symptom_logs CRUD
│   │   ├── feedback_repo.py
│   │   ├── user_repo.py
│   │   └── admin/
│   │       ├── consultation_admin_repo.py
│   │       ├── dashboard_repo.py
│   │       ├── feedback_admin_repo.py
│   │       └── kb_repo.py
│   ├── services/                     ← business logic (use repos + clients)
│   │   ├── auth_service.py
│   │   ├── prediction_service.py     ← UC-01/03/04/05 orchestrator
│   │   ├── preprocess_service.py     ← clean_text + extract_symptoms
│   │   ├── triage_service.py         ← (legacy) standalone triage helpers
│   │   ├── explain_service.py        ← UC-04 SHAP/LIME wrapper
│   │   ├── kb_service.py             ← KB lookup + bilingual hydration
│   │   ├── pdf_service.py            ← UC-06 reportlab PDF builder
│   │   └── admin/
│   │       ├── bootstrap.py          ← one-shot super-admin row creator
│   │       ├── dashboard_service.py
│   │       ├── consultation_service.py
│   │       ├── feedback_admin_service.py
│   │       ├── kb_admin_service.py
│   │       └── user_admin_service.py
│   ├── clients/
│   │   ├── ml_client.py              ← loads sklearn pipeline + stub fallback
│   │   └── kb_client.py              ← (legacy) wrapper over disease_kb table
│   └── utils/
│       ├── helpers.py
│       └── validators.py
├── models/                           ← serialised .joblib + .pkl ML artefacts
├── seed_disease_kb.py                ← run-once KB bootstrap script
├── requirements.txt
├── .env.example                      ← every supported env var
├── BACKEND.md                        ← THIS FILE
├── CHANGELOG.md
├── CLAUDE.md                         ← project memory
└── DESIGN.md                         ← frontend architecture
```

### Layer rules

1. **Endpoints (`api/v1/endpoints/*`)** are thin: validate input via Pydantic,
   delegate to a service, return the response model. **No SQL, no business
   logic.**
2. **Services (`services/*`)** orchestrate. They may call repos, ml_client,
   kb_service, etc. Business rules live here.
3. **Repositories (`repositories/*`)** are query-only. One repo per table
   (or per admin concern). They never know about Pydantic.
4. **Models (`models/*`)** are *Pydantic* schemas — request/response shapes.
   ORM tables live in `db/models.py` and **never** leak past the repo layer.

---

## 4. Configuration — every env var

Defined once in `app/core/config.py` and loaded from `.env`. Don't read
`os.environ` from anywhere else.

| Var | Default | Notes |
|---|---|---|
| `APP_NAME` | `DevloCare API` | Surfaced in `/` root and Swagger |
| `APP_VERSION` | `1.0.0` | |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `DATABASE_URL` | `sqlite+aiosqlite:///./devlocare.db` | Use `postgresql+asyncpg://...` in prod |
| `JWT_SECRET_KEY` | placeholder | Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` (8 h) | Pair with frontend idle timeout |
| `DEFAULT_LANGUAGE` | `English` | `English` / `Urdu` |
| `SUPER_ADMIN_USERNAME` | `""` | First-boot bootstrap; blank skips it |
| `SUPER_ADMIN_PASSWORD` | `""` | |
| `SUPER_ADMIN_EMAIL` | `""` | |
| `RATE_LIMIT_ENABLED` | `True` | Set false in tests |
| `RATE_LIMIT_LOGIN` | `5/minute` | slowapi syntax `<count>/<period>` |
| `RATE_LIMIT_REGISTER` | `10/hour` | |
| `RATE_LIMIT_PREDICT` | `30/minute` | |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated, or `*` in dev |

---

## 5. Auth & RBAC

### Token shape

`access_token` — JWT (HS256) returned by `/auth/register`, `/auth/login`,
`/auth/token`. Body:

```json
{
  "sub": "42",            // user_id as string
  "username": "fahad",
  "is_admin": false,
  "is_super_admin": false,
  "exp": 1717000000,
  "iat": 1716999000
}
```

8 h expiry by default. The frontend layers a 30 min idle timeout on top.

### Dependencies

- `get_current_user` — required JWT, returns `User` ORM row.
- `get_current_user_optional` — present-but-no-token returns `None`. Used
  by `/predict/text` and `GET /history` so guests can call them.
- `require_admin` — 403 unless `is_admin OR is_super_admin`.
- `require_super_admin` — 403 unless `is_super_admin`.

### Roles

| Role | Capabilities |
|---|---|
| **Guest** | Submit symptoms, view results, read disclaimer. No saved history. |
| **Registered** | Guest features + saved history, profile, language, PDF export, feedback submission. |
| **Admin** | Read-only access to all consultations, feedback, dashboard. Full KB CRUD. |
| **Super-admin** | Admin + create/promote/demote admin users. Cannot be granted via API — bootstrapped from env on first boot only. |

### Super-admin bootstrap

`app/services/admin/bootstrap.py::ensure_super_admin` runs once in the
`lifespan` startup. If a row with `is_super_admin=True` already exists it's
a no-op; otherwise it reads `SUPER_ADMIN_*` env vars and inserts the row.
Leaving the env vars blank skips bootstrap (useful for read-only / migration
processes).

### Password storage

Bcrypt via `passlib` (12 rounds). `auth_service.register_user` hashes; never
stored or transported in plaintext.

### PII rejection

`SymptomTextRequest` validator runs four regexes on the input:

| Pattern | Catches |
|---|---|
| `[\w.+-]+@[\w-]+\.[\w.-]+` | Email |
| `\b\d{5}-\d{7}-\d\b` | Pakistani CNIC |
| `(?:\d[\s-]?){13,19}` | Card-like / long-number runs |
| `\+?\d[\d\s\-()]{6,}\d` | Phone |

A match raises `ValueError` → FastAPI returns 422. The frontend mirrors
these regexes client-side so the user sees the error inline before the
network call.

---

## 6. Rate limiting

`app/core/rate_limit.py` exposes a shared `Limiter` keyed by client IP
(`slowapi.util.get_remote_address`). Endpoints decorate their routes:

```python
@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, payload: UserLoginRequest, ...):
    ...
```

Note the explicit `request: Request` parameter — slowapi inspects it.

### Limits in place

| Endpoint | Default limit |
|---|---|
| `POST /auth/login` and `/auth/token` | 5/minute |
| `POST /auth/register` | 10/hour |
| `POST /predict/text` | 30/minute |

When exceeded the registered handler returns:

```json
{
  "code": "rate_limited",
  "message": "Too many requests — please slow down and try again.",
  "detail": "5 per 1 minute"
}
```

at HTTP 429. The frontend's `ApiError.isRateLimited()` recognises this and
shows a toast.

---

## 7. CORS

`app/main.py` adds `CORSMiddleware` with:

- `allow_origins=settings.cors_origins_list` (parsed from `CORS_ORIGINS`)
- `allow_credentials=True`
- `allow_methods=["*"]`, `allow_headers=["*"]`
- `expose_headers=["Content-Disposition", "Content-Length", "X-Export-Rows"]`
  so the SPA can read PDF download metadata.

Pass `CORS_ORIGINS=*` only in dev. Production should list explicit origins.

---

## 8. API reference

All endpoints under `/api/v1`. Bearer token in `Authorization: Bearer <jwt>`
where required.

### Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | none | Pings the DB; returns `{status, db}`. 503 if unreachable. |

### Auth

| Method | Path | Auth | Body / Response |
|---|---|---|---|
| POST | `/auth/register` | none | Body: `UserRegisterRequest`; returns `TokenResponse`. **Rate-limited.** |
| POST | `/auth/login` | none | Body: `UserLoginRequest` (JSON, accepts username **or** email). **Rate-limited.** |
| POST | `/auth/token` | none | Form-encoded OAuth2 password flow (Swagger Authorize button uses this). **Rate-limited.** |
| GET | `/auth/me` | JWT | Returns `UserResponse`. |
| PUT | `/auth/me` | JWT | Body: `UserUpdateRequest` (age / sex / language_pref). |

### Prediction (UC-01/03/04/05)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/predict/text` | optional | Body: `SymptomTextRequest`. Returns `PredictionResponse`. Guests get prediction without log persistence. **Rate-limited.** |

### History (UC-06)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/history` | optional | Paginated list. Guests receive empty + login hint. |
| GET | `/history/{log_id}` | JWT | **Owner-guarded.** Returns full `PredictionResponse` rebuilt from `explanation_json`. 404 if not yours. |
| DELETE | `/history/{log_id}` | JWT | Owner-guarded permanent delete. |
| POST | `/history/export` | JWT | Body: `HistoryExportRequest` (`log_ids[]` **or** `last_n`). Returns `application/pdf` stream. |

### Feedback (UC-08)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/feedback` | JWT | Body: `FeedbackCreateRequest` (1–5 rating + optional comment). Idempotent: re-submission updates the existing row (UNIQUE on `(log_id, user_id)`). |
| GET | `/feedback/monitor` | JWT | Aggregate stats. **TODO**: tighten to admin-only. |

### Admin — Dashboard

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/dashboard` | admin | Single combined payload: counters + top diseases + triage donut + language donut. |
| GET | `/admin/dashboard/counters` | admin | Headline KPIs only. |
| GET | `/admin/dashboard/top-diseases` | admin | `?limit=N` (1–25). |
| GET | `/admin/dashboard/triage-distribution` | admin | |
| GET | `/admin/dashboard/language-usage` | admin | |

### Admin — Consultations

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/consultations` | admin | `?disease=&triage=&language=&limit=&offset=`. Paginated. |
| GET | `/admin/consultations/{log_id}` | admin | Full detail incl. SHAP summary + attached feedback. |

### Admin — Feedback

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/feedback/summary` | admin | total, average, % positive, rating distribution. |
| GET | `/admin/feedback` | admin | Paginated recent feedback rows. |

### Admin — Knowledge base (UC-07)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/diseases` | admin | `?search=&limit=&offset=`. |
| GET | `/admin/diseases/{id}` | admin | |
| POST | `/admin/diseases` | admin | Body: `DiseaseKBCreateRequest`. |
| PUT | `/admin/diseases/{id}` | admin | Body: `DiseaseKBUpdateRequest` (partial). `None` = leave unchanged. |
| DELETE | `/admin/diseases/{id}` | admin | |

### Super-admin — User management

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/admin/users/create-admin` | super-admin | Body: `CreateAdminRequest`. Creates admin with `is_admin=True`. |
| POST | `/admin/users/{user_id}/promote` | super-admin | Set `is_admin=True` on existing user. |
| POST | `/admin/users/{user_id}/demote` | super-admin | Set `is_admin=False`. Cannot demote a super-admin. |

---

## 9. Database schema

SQLite in dev (file `devlocare.db`), Postgres in prod via `DATABASE_URL`.
ORM definitions in `app/db/models.py`. Async engine in `app/db/session.py`.

### Tables

#### `users`

| Column | Type | Constraints |
|---|---|---|
| `user_id` | INT PK auto | |
| `username` | VARCHAR(50) | UNIQUE, NOT NULL, indexed |
| `password_hash` | VARCHAR(255) | NOT NULL |
| `email` | VARCHAR(100) | UNIQUE, NOT NULL, indexed |
| `language_pref` | VARCHAR(10) | default `English` |
| `age` | INT | nullable |
| `sex` | VARCHAR(10) | nullable |
| `is_admin` | BOOL | NOT NULL default false |
| `is_super_admin` | BOOL | NOT NULL default false |
| `created_at` | DATETIME | UTC timestamp |

#### `symptom_logs`

| Column | Type | Notes |
|---|---|---|
| `log_id` | INT PK auto | |
| `user_id` | INT FK → users (ON DELETE SET NULL) | nullable for guest logs |
| `raw_text` | TEXT | original symptom input |
| `transcription` | TEXT | reserved for UC-02 voice |
| `predicted_condition` | VARCHAR(100) | English name of top-1 |
| `confidence_score` | FLOAT | top-1 probability |
| `explanation_json` | TEXT | JSON blob: top_conditions[], extracted_symptoms[], explanation, metadata, language, triage_rule |
| `triage_level` | VARCHAR(20) | `self_care` / `see_gp` / `urgent_care` |
| `created_at` | DATETIME | indexed |

The full bilingual + explainability bundle lives inside `explanation_json`
so `GET /history/{log_id}` can rehydrate a `PredictionResponse` without
re-running the model.

#### `disease_kb`

| Column | Notes |
|---|---|
| `disease_id` | PK |
| `name_en` | NOT NULL, indexed |
| `name_ur` | bilingual UI |
| `specialist_type` | "Cardiologist", "Pediatrician", … |
| `triage_category` | self_care / see_gp / urgent_care |
| `care_tips_en` | TEXT |
| `care_tips_ur` | TEXT |
| `red_flags` | TEXT (newline / pipe / semicolon / comma split) |
| `updated_at` | |

Seed via `python seed_disease_kb.py`.

#### `consultation_feedback`

| Column | Notes |
|---|---|
| `feedback_id` | PK |
| `log_id` | FK → symptom_logs ON DELETE CASCADE |
| `user_id` | FK → users |
| `helpfulness_rating` | INT, CHECK 1..5 |
| `feedback_text` | TEXT, max 1000 chars |
| `created_at` / `updated_at` | |

UNIQUE(`log_id`, `user_id`) — re-submission overwrites.

---

## 10. Prediction pipeline (UC-01/03/04/05)

`app/services/prediction_service.py::predict_from_text` is the single
entry point. Steps:

1. **Resolve language.** Registered users get `users.language_pref`, guests
   get English. Drives KB hydration + disclaimer + `name`.
2. **Preprocess.** `preprocess_service.clean_text` (lowercase, strip
   punctuation, normalise whitespace) and `extract_symptoms` (rule-based
   spaCy patterns + curated synonyms).
3. **ML predict.** `ml_client.predict_top_k(text, k=3)` returns calibrated
   probabilities. Falls back to a stub when no model artefact is found
   (`ml_client.is_stub()` exposed for the explanation panel and admin).
4. **KB enrichment** (single bulk query — all top-K names hydrated at once)
   gives bilingual care tips, triage_category, specialist_type, red_flags.
5. **Smart triage rules (UC-03)** evaluated in priority order:
   1. critical keyword in input → `urgent_care`
   2. pregnancy + bleeding/severe-pain/fever/abdominal-pain → `urgent_care`
   3. age <5 or >65 with severity moderate/severe → `urgent_care`
   4. chronic disease + severity severe → `urgent_care`
   5. KB triage urgent_care → `urgent_care`
   6. severity severe → `urgent_care`
   7. KB triage see_gp → `see_gp`
   8. severity moderate → `see_gp`
   9. KB triage self_care → `self_care`
   10. default → `self_care`
   The fired rule is logged in `explanation_json.triage_rule`.
6. **Explainability (UC-04)** — `explain_service.get_explanation` runs SHAP
   on the calibrated classifier with the top-condition class index. Output:
   `rationale` sentence + `key_symptoms[]` + `feature_importance[]` ready
   for the frontend bar chart.
7. **Persist** to `symptom_logs` (skip when `user_id` is None, but actually
   we still write the row with `user_id=NULL` for guest analytics).
8. **Return** `PredictionResponse` with bilingual care tips, top conditions,
   triage, recommended specialist, red flags, explanation, language-specific
   disclaimer, and the new `log_id`.

**Performance target** (FRD): ≤ 2.5 s on text queries. The bulk KB lookup +
single SHAP run keeps us in budget.

---

## 11. PDF export (UC-06)

`pdf_service.build_history_pdf(logs, username)` uses reportlab to render:

- Cover page: app name, generated_at, username, disclaimer.
- One section per log: timestamp, raw text, top-3 conditions with confidence
  bars, triage badge, recommended specialist, care tips (in user's language),
  red flags, key symptoms.
- Footer with a per-page disclaimer.

Endpoint streams the bytes with `Content-Disposition: attachment;
filename=devlocare-consultations-<user>-<utc>.pdf`. Frontend's
`useExportPdf` reads `Content-Disposition` to honour the server filename.

---

## 12. Errors & responses

Errors are normalised via `app/core/exceptions.py`. The shape:

```json
{ "code": "history_not_found", "message": "Consultation not found" }
```

at the appropriate HTTP status. Validation errors from FastAPI's
`RequestValidationError` come back as:

```json
{
  "detail": [
    { "loc": ["body", "username"], "msg": "...", "type": "value_error" }
  ]
}
```

The frontend's `ApiError` knows how to consume both shapes (see
`src/lib/api/errors.ts`).

---

## 13. Logging

Stdlib `logging` via `app/core/logging.py::get_logger`. Conventions:

- **`logger.info(...)`** for normal flow.
- **`logger.warning(...)`** for ownership / 4xx outcomes that aren't bugs.
- **`logger.exception(...)`** for unexpected exceptions.
- **`logger.info("AUDIT ...", ...)`** for sensitive ops (history delete,
  PDF export, feedback submit, admin promote/demote, KB CRUD). Grep these
  later for compliance review.

---

## 14. Run / build / test

### Local dev (Windows, PowerShell)

```powershell
cd C:\Users\MuhammadZaidRehan\devlocare-backend-feat-admin-panel-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
notepad .env   # fill JWT_SECRET_KEY + super-admin
uvicorn app.main:app --reload
# → http://localhost:8000
# Swagger: http://localhost:8000/docs
```

### Generate a JWT secret

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### Seed the KB

```powershell
python seed_disease_kb.py
```

### Production (sketch)

- Set `DATABASE_URL=postgresql+asyncpg://...`
- Set `ENVIRONMENT=production`
- Set explicit `CORS_ORIGINS` (no `*`)
- Run behind a reverse proxy that injects `X-Forwarded-For` and trust it
  via uvicorn `--proxy-headers --forwarded-allow-ips`
- Persist `JWT_SECRET_KEY` in a secret manager
- Use Alembic for schema migrations (config present, hasn't been used yet)

---

## 15. House rules

1. **Endpoints stay thin.** No SQL or business logic in `endpoints/*`.
2. **No bare `Exception` re-raised.** Either let it propagate to FastAPI's
   500 handler, or convert to a custom `ApiError` subclass.
3. **Logs first, errors second.** Every audit-worthy event gets a
   `logger.info("AUDIT …", …)` before the response is built.
4. **Pydantic for everything that crosses a boundary.** No raw dicts on the
   wire.
5. **Async all the way.** Use the async DB session; don't mix sync drivers.
6. **Ownership guards return 404.** Leaking the existence of foreign
   `log_id`s is worse than a vague error.
7. **PII rejection is server + client.** Both sides keep the same regex set.
8. **Super-admin is bootstrap-only.** No code path mints one through the
   API. Period.
9. **Migrations** — once the schema is non-trivial, pin it with Alembic.
   Until then `init_db()` creates tables on first run.

---

## 16. Pending / TODO

- [ ] **`GET /feedback/monitor` admin guard.** Currently auth-only — replace
      with `require_admin`.
- [ ] **Alembic migrations.** Wire `alembic init`, generate baseline
      revision, switch CI to `alembic upgrade head`.
- [ ] **Voice (UC-02).** Add `POST /predict/voice` with Whisper-tiny on the
      backend; reuse the same `prediction_service` pipeline.
- [ ] **Self-harm safety event log.** Optional audit table that stores when
      the safety guard fires (frontend currently handles it client-side).
- [ ] **Redis caching** (FRD §6 tools section). Cache the bulk KB lookup
      for hot conditions.
- [ ] **OpenAPI codegen for frontend.** Frontend currently hand-mirrors
      Pydantic — generating from `/openapi.json` would eliminate drift.
- [ ] **Test suite.** Skeleton in `requirements.txt` is fine; pytest +
      `httpx.AsyncClient` integration tests would prove it works.
- [ ] **Rate-limit `Retry-After` header** in the 429 handler so the SPA can
      back off intelligently.
- [ ] **httpOnly auth cookies** + CSRF token (post-MVP migration).

---

## 17. Quick reference for new contributors

> **"I want to add an endpoint."**
> 1. Define request/response in `app/models/{request,response}.py`.
> 2. Add a thin route in `app/api/v1/endpoints/<area>.py`.
> 3. Delegate to a service in `app/services/<area>_service.py`.
> 4. If you need new SQL, add a function to a repo in `app/repositories/`.
> 5. Update the API table in this doc.

> **"I want to extend the prediction."**
> Edit `app/services/prediction_service.py`. Triage rules live in
> `_compute_triage`. KB enrichment goes through `kb_service.bulk_lookup`.

> **"I want to add a setting."**
> 1. Add it to `Settings` in `app/core/config.py` with a sensible default.
> 2. Document it in `.env.example` and section 4 above.
> 3. Use `from app.core.config import settings` everywhere — never read
>    `os.environ` directly.

> **"I want to add a permission tier."**
> Add a column to `users`, a flag to the JWT payload, and a new dep in
> `app/api/v1/admin_deps.py`. Decorate routes with it.
