# Backend — CHANGELOG

> [Keep a Changelog](https://keepachangelog.com/) format.
> Versioning roughly follows [SemVer](https://semver.org/).
>
> Companion: [`BACKEND.md`](./BACKEND.md), [`CLAUDE.md`](./CLAUDE.md).

---

## [Unreleased]

Tracking work that's queued but not yet shipped:

- Admin guard on `GET /feedback/monitor` (currently auth-only).
- Alembic baseline migration + CI `alembic upgrade head` step.
- `POST /predict/voice` (UC-02) with Whisper-tiny.
- `safety_events` table + endpoint to persist self-harm detections from the
  frontend client-side guard, for audit/QA.
- Redis caching layer for KB hot conditions (FRD §6 tools).
- pytest + httpx.AsyncClient integration test suite.
- httpOnly cookie auth + CSRF token (post-MVP migration).

---

## [1.2.0] — 2026-05-03 — UX completeness round

Added the endpoint the frontend's history-detail page needs, plus the
slowapi rate-limiting required by the FRD's tools section.

### Added
- **`GET /history/{log_id}`** — owner-guarded route returning the full
  `PredictionResponse` rebuilt from the persisted `explanation_json`
  blob. Returns 404 if the log doesn't exist or isn't owned by the
  caller (no foreign-id leakage). New helper
  `app/api/v1/endpoints/history.py::_hydrate_prediction` parses the
  stored JSON via Pydantic with safe fallbacks for older / partial rows.
- **Rate limiting via slowapi** (FRD §6 tools).
  - New module `app/core/rate_limit.py` exposes a shared `Limiter`
    keyed by client IP plus a 429 handler that emits our normalised
    error envelope (`code: "rate_limited"`).
  - `POST /auth/login` and `/auth/token` are limited to
    `RATE_LIMIT_LOGIN` (default 5/minute).
  - `POST /auth/register` is limited to `RATE_LIMIT_REGISTER` (default
    10/hour).
  - `POST /predict/text` is limited to `RATE_LIMIT_PREDICT` (default
    30/minute).
  - All limits configurable via env, and the whole subsystem can be
    disabled with `RATE_LIMIT_ENABLED=false` (useful in tests).
- `slowapi` added to `requirements.txt`.
- `.env.example` documents the four new env vars.

### Changed
- `app/main.py` registers the limiter, `SlowAPIMiddleware`, and the
  `RateLimitExceeded` exception handler before route inclusion.
- Auth + predict route signatures take an explicit `request: Request`
  parameter so slowapi can inspect the request state.

### Notes
- **Migration step required**: run `pip install -r requirements.txt`
  once to pick up `slowapi`, then restart `uvicorn`.
- The 429 response shape is intentionally aligned with the rest of the
  API so the frontend's `ApiError.isRateLimited()` works without
  special-casing.

---

## [1.1.0] — 2026-05-03 — Frontend integration prep

Add the cross-cutting concerns the SPA needs to talk to us cleanly.

### Added
- **CORS middleware** in `app/main.py` reading from `CORS_ORIGINS`
  (comma-separated, or `*` for dev). Exposes
  `Content-Disposition`, `Content-Length`, and `X-Export-Rows` so the
  frontend's PDF export can read filename + size headers.
- `cors_origins_list` parsed property on `Settings`.
- `.env.example` documents `CORS_ORIGINS` with localhost defaults.

### Changed
- **`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`** default lowered from 1440
  (24 h) → **480 (8 h)**. Frontend layers a 30 min idle timeout on top
  for additional protection. `.env.example` updated.

### Why
- Without CORS the SPA was getting browser-blocked even when both
  servers ran on the same machine. Different ports = different origins.
- 8 h is a sensible production middle-ground for medical software —
  short enough that a stolen token isn't useful for a full day; long
  enough that an active user isn't kicked mid-session.

---

## [1.0.0] — Initial production-grade backend (pre-handover baseline)

Snapshot of what was already in place before the handover. Documented
here so the changelog reads continuously.

### Core platform
- **FastAPI 0.x** app with async lifespan; uvicorn ASGI server.
- **Async SQLAlchemy** + **aiosqlite** (dev) / **asyncpg** (prod) via
  `DATABASE_URL`.
- **Pydantic v2** schemas for every request and response; Pydantic
  Settings for env config.
- **JWT (PyJWT)** access tokens, HS256, configurable expiry.
- **bcrypt** password hashes via `passlib`.
- Centralised exception handlers + `ApiError` envelope.
- Structured logging (`get_logger`) with `AUDIT …` lines for sensitive
  ops.
- Versioned router under `/api/v1`.

### Endpoints
- **Health** — `GET /health` pings the DB.
- **Auth** — `register`, `login`, `token` (OAuth2 form for Swagger),
  `me` (GET + PUT).
- **Predict (UC-01/03/04/05)** — `POST /predict/text`, optional auth.
- **History (UC-06)** — paginated `GET`, `DELETE /{log_id}` with owner
  guard, `POST /export` returning a reportlab PDF stream.
- **Feedback (UC-08)** — `POST` (idempotent on `(log_id, user_id)`),
  `GET /monitor` aggregates.
- **Admin** sub-router under `/admin/*`:
  - `dashboard` (combined + per-widget endpoints).
  - `consultations` (paginated list + detail with SHAP summary).
  - `feedback` (summary + list).
  - `diseases` knowledge-base CRUD (UC-07).
  - `users` (super-admin only: create-admin, promote, demote).

### Security & safety
- RBAC via `is_admin` / `is_super_admin` columns + `require_admin`,
  `require_super_admin` deps.
- One-shot super-admin bootstrap from `SUPER_ADMIN_*` env vars on first
  boot. **No API path** mints a super-admin.
- Server-side PII regex on `POST /predict/text` (email / CNIC / card /
  phone). Mirror of the frontend client-side guard.
- Ownership-guarded reads for history + feedback (404 not 403, to avoid
  leaking foreign log_ids).

### Domain logic
- **Smart triage rules** in `prediction_service._compute_triage` evaluated
  in priority order (critical keywords > pregnancy escalation > vulnerable
  ages > chronic-disease severity > KB triage > severity > default).
  The fired rule is logged inside `explanation_json.triage_rule` for
  audit.
- **KB enrichment** via single bulk lookup over the predicted top-K so
  hydration is one SQL round-trip.
- **Explainability (UC-04)** wraps the trained classifier in a SHAP
  explainer and packages `rationale + key_symptoms + feature_importance`
  ready for the frontend bar chart.
- **PDF export (UC-06)** with reportlab, language-localised disclaimer,
  per-log section, professional formatting.
- **Bilingual care tips** stored in `disease_kb.care_tips_{en,ur}` and
  surfaced in `PredictionResponse.care_tips`.

### Data
- Tables: `users`, `symptom_logs`, `disease_kb`, `consultation_feedback`.
- `seed_disease_kb.py` populates the KB from a curated list.
- Trained ML artefacts under `models/` (joblib pipeline + sentence-BERT
  embeddings).

### Conventions
- Endpoints stay thin (validate + delegate).
- Services orchestrate business logic.
- Repositories are query-only.
- Pydantic for everything that crosses an HTTP or DB boundary.
- `AUDIT …` log prefix for every mutating sensitive operation.
