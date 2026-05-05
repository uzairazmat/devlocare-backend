# CLAUDE.md — DevloCare project memory

> Persistent context for any Claude/AI session working on this repo. Read this
> first; the rest of the codebase makes more sense once these facts are loaded.

---

## 1. What this project is

**DevloCare** — an AI-Powered Smart Health Assistant. It accepts a free-text
(or optional voice) description of symptoms and returns the **Top-3 likely
conditions** with confidence, a **triage level** (`self_care` / `see_gp` /
`urgent_care`), a **recommended specialist**, **bilingual care tips**, **red
flags**, and a **SHAP/LIME-style explanation** of *why* the model decided what
it decided. UI is bilingual (English / Urdu) with full RTL.

This is our **Final Year Project (FYP)** but **built to production grade** —
not academic-only. Every architectural decision should pass a real-world
review: env-driven config, RBAC, audit logs, accessibility, performance,
typed contracts, code-split routes, virtualized lists, dark/light themes,
i18n, no leaked PII.

| Field | Value |
|------|-------|
| Domain | Web App (FastAPI backend + React SPA frontend) |
| Supervisor | Shakeel Saeed — `shakeel@vu.edu.pk` (MS Teams: `shakeelsaeedvu@outlook.com`) |
| Targets | ~80% top-3 accuracy · ~90% specialist suggestion · 95% explainable outputs · ≤2.5s text response · 99% demo uptime · ≥90% STT on clean audio |
| Safety | Every result page shows the medical disclaimer in the user's chosen language. Never claim diagnosis. |

---

## 2. Repository layout (after frontend scaffold)

```
devlocare-backend-feat-admin-panel-backend/   ← repo root
├── app/                   ← FastAPI backend (DONE)
│   ├── api/v1/endpoints/  ← auth, predict, history, feedback, admin/*
│   ├── services/          ← prediction, triage, explain, pdf, kb, admin/*
│   ├── repositories/      ← log, feedback, user, admin/*
│   ├── db/                ← SQLAlchemy models + async session
│   ├── core/              ← config, security, logging, exceptions
│   ├── clients/           ← ml_client, kb_client
│   ├── models/            ← request.py, response.py, admin.py (Pydantic v2)
│   └── utils/
├── models/                ← trained ML/NLP artifacts
├── seed_disease_kb.py     ← KB bootstrap
├── requirements.txt
├── .env.example
├── DESIGN.md              ← frontend architecture spec (single source of truth)
├── CLAUDE.md              ← THIS FILE
└── devlocare-frontend/    ← React 18 + Vite SPA (scaffolded)
    └── (see DESIGN.md for full tree)
```

> **Note on layout.** The frontend currently lives as a sub-folder so it can
> share the same git repo and CI. Moving it to a sibling repo (`../devlocare-frontend`)
> is a `git mv` away; nothing in the code assumes one or the other.

---

## 3. Backend status (already shipped)

The backend is **complete** and exposes the following surface, all under
`/api/v1`. JWT bearer auth (HS256) on protected routes; ownership-guarded
queries; super-admin bootstrapped from env at first boot.

### Public / patient
| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | DB ping |
| `POST` | `/auth/register` | Create user, returns JWT |
| `POST` | `/auth/login` | Login (username **or** email), returns JWT |
| `POST` | `/auth/token` | OAuth2 form flow (for Swagger Authorize) |
| `GET`  | `/auth/me` | Current user profile |
| `PUT`  | `/auth/me` | Update age/sex/`language_pref` |
| `POST` | `/predict/text` | UC-01/03/04/05: symptoms → top-3 + triage + specialist + tips + red flags + SHAP. Auth optional (guests get prediction, no log). |
| `GET`  | `/history` | Paginated sidebar list (auth required; guests get empty + login hint) |
| `DELETE` | `/history/{log_id}` | Owner-only delete |
| `POST` | `/history/export` | PDF export by `log_ids` **or** `last_n` |
| `POST` | `/feedback` | UC-08: submit/idempotent-update 1-5 rating + comment for own log |
| `GET`  | `/feedback/monitor` | Aggregate feedback stats (currently auth-only — TODO add admin guard) |

### Admin (`require_admin`)
| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/admin/dashboard` | Combined payload (counters + top diseases + triage + language) |
| `GET`  | `/admin/dashboard/counters` · `/top-diseases` · `/triage-distribution` · `/language-usage` | Per-widget polls |
| `GET`  | `/admin/consultations` | Paginated list (filter by `disease`, `triage`, `language`) |
| `GET`  | `/admin/consultations/{log_id}` | Full detail incl. SHAP summary, top-3, attached feedback |
| `GET`  | `/admin/feedback/summary` | Cards: total, avg, %positive |
| `GET`  | `/admin/feedback` | Paginated recent feedback |
| `GET`  | `/admin/diseases` | KB list (paginated, `search`) |
| `GET`/`POST`/`PUT`/`DELETE` | `/admin/diseases/{id}` | KB CRUD |

### Super-admin only (`require_super_admin`)
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/admin/users/create-admin` | Provision new admin |
| `POST` | `/admin/users/{user_id}/promote` | Normal user → admin |
| `POST` | `/admin/users/{user_id}/demote` | Admin → normal user |

> Super-admin status is **never** API-grantable. It is bootstrapped exactly
> once from `SUPER_ADMIN_*` env vars on first boot.

### Key contract types (mirror these in TS)
- `TriageLevel = "self_care" | "see_gp" | "urgent_care"`
- `Language = "English" | "Urdu"`
- `Sex = "Male" | "Female" | "Other"`
- `Severity = "mild" | "moderate" | "severe"`
- `PredictionResponse` returns `top_conditions[]`, `triage_level`,
  `recommended_specialist`, `care_tips {en, ur}`, `red_flags[]`,
  `extracted_symptoms[]`, `explanation { rationale, key_symptoms[],
  feature_importance[], confidence_breakdown }`, `disclaimer`, `log_id?`,
  `language`.
- `User` exposes `is_admin` and `is_super_admin` — use these for routing.

### Hard PII rules (enforced server-side, replicate client-side)
Free-text input is rejected if it matches any of: email pattern, Pakistani
CNIC `#####-#######-#`, 13–19 digit card-like runs, phone-shaped strings.
Show the user a friendly inline error before submit.

---

## 4. Frontend stack (decided)

| Concern | Choice | Why |
|---|---|---|
| Framework | **React 18 + TypeScript + Vite 5** | Fast HMR, ESM, smallest bundle for SPA |
| Styling | **Tailwind CSS** + CSS variables for design tokens | Token-driven theming; no raw hex in components |
| Routing | **React Router v6** with `createBrowserRouter` + `lazy()` per route | Code-split every screen |
| Server state | **TanStack Query v5** | SWR cache, retry, background refetch, infinite/paginated history |
| Client state | **Zustand** (sliced stores, `persist` only for theme + language) | Tiny, no boilerplate, plays nice with Query |
| Forms | **React Hook Form + Zod** | Strict typing, low re-renders |
| HTTP | **Axios** instance w/ JWT interceptor + 401 refresh-or-logout | Single chokepoint for auth + errors |
| i18n | **i18next + react-i18next** | EN/UR catalogs, RTL via `dir` attr |
| Charts | **Recharts** for admin (responsive) + **Chart.js** for explainability bars | Both are in the FRD |
| Virtualization | **@tanstack/react-virtual** | History list, KB table, consultations table |
| Icons | **lucide-react** | Light, tree-shakable |
| Testing | **Vitest + React Testing Library + Playwright** | Unit + integration + e2e |
| Lint/format | **ESLint + Prettier + TypeScript strict** | CI gates |
| Auth storage | JWT in `httpOnly` cookie *if* backend adds it later; for now an in-memory store + `sessionStorage` fallback (never `localStorage` for tokens) | XSS-resistant baseline |

**All values configurable via `import.meta.env.VITE_*` — never inline a URL,
key, or feature flag in components.**

See `DESIGN.md` for the complete folder tree, providers, hooks catalogue,
and module-by-module screen plan.

---

## 5. Universal design system (FROZEN — do not drift)

Tokens live in `src/styles/tokens.css` and map to Tailwind via
`tailwind.config.ts`. Components consume them through Tailwind utilities
that resolve to CSS variables (e.g. `bg-app`, `text-body`, `border-strong`).

- Primary brand: forest `#285A48` (light) / `#408A71` (dark).
- Typography: **Inter** body, optional Manrope/Plus Jakarta for the wordmark.
- Radii: buttons/inputs `rounded-xl`; cards/modals/bubbles `rounded-2xl`;
  pills `rounded-full`.
- Shadows: `shadow-sm` / `hover:shadow-md` in light; **borders, not glows**,
  in dark.
- Semantic states: `success #16A34A/#22C55E`, `warning #D97706/#F59E0B`,
  `danger #DC2626/#F87171`, `info #0EA5A4/#2DD4BF`.
- Chart palette is fixed to forest / sage / mint / amber / red (in that
  order). Never use random palettes.
- The mood is **breathable, clinical, intelligent, calm, premium**.
  Whitespace is part of the design.

---

## 6. House rules

1. **No hardcoded URLs, secrets, model paths, or feature flags.** Read from
   `import.meta.env.VITE_*`. Add it to `.env.example` and to the typed
   `src/config/env.ts` schema (Zod-validated at boot).
2. **No raw hex in components.** Always go through Tailwind tokens.
3. **No `any`.** Use the generated API types in `src/types/api.ts`.
4. **Every list with potential >50 rows is virtualized.** History sidebar,
   admin consultations, admin feedback, KB list — all use `react-virtual`.
5. **Memo the right thing.** Use `useMemo` for derived data, `useCallback`
   for stable callbacks passed to memoized children, `React.memo` for pure
   row components in virtualized lists. Don't memo everything.
6. **`useRef` for**: scroll containers, focus management (autofocus the
   composer after a prediction returns), debounced input timers, intersection
   observers (infinite history scroll), audio recorder handles.
7. **Routes are lazy.** `const AdminDashboard = lazy(() => import(...))`.
   Wrap with a `<Suspense>` skeleton.
8. **Every async UI has all four states**: loading skeleton, success, empty,
   error (with retry). No spinner-of-death.
9. **Accessibility is non-negotiable.** Semantic HTML, focus rings on all
   interactive elements, `aria-live` for streaming responses, keyboard nav
   for the chat composer, color contrast ≥ AA.
10. **Disclaimer on every result page**, in the user's language.
11. **PII is rejected client-side** before the request goes out — duplicate
    of the server regex in `src/lib/validation/pii.ts`.
12. **Never store JWT in `localStorage`.** In-memory store; optional
    `sessionStorage` fallback for survives-refresh; aim for httpOnly cookie
    when the backend adds CSRF.

---

## 7. Run / build / test

### Backend (already wired)
```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill JWT_SECRET_KEY + super-admin
uvicorn app.main:app --reload
# → http://localhost:8000/docs
```

### Frontend
```bash
cd devlocare-frontend
cp .env.example .env.development   # set VITE_API_BASE_URL=http://localhost:8000/api/v1
npm install
npm run dev          # http://localhost:5173
npm run build        # production bundle in dist/
npm run preview      # smoke-test the production bundle
npm run lint         # ESLint
npm run typecheck    # tsc --noEmit
npm run test         # Vitest
npm run test:e2e     # Playwright (after `npm run build && npm run preview`)
```

---

## 8. What's left (high-level roadmap)

- [x] Backend API (auth, predict, history, feedback, admin, super-admin)
- [x] Universal design system (tokens, type, radii, shadows)
- [x] Frontend scaffolding (Vite + React + TS + Tailwind + providers)
- [ ] Patient module — chat composer, prediction result card, explanation
      panel, history sidebar (virtualized), settings, PDF export
- [ ] Admin module — dashboard, consultations table + detail drawer,
      feedback monitor, KB CRUD, super-admin user mgmt
- [ ] Voice STT (UC-02) — Whisper-tiny on backend or Web Speech on client
- [ ] i18n catalogues — translate every string EN ↔ UR
- [ ] e2e tests covering: register → predict → save → export PDF
- [ ] Lighthouse pass (Performance ≥ 90, A11y ≥ 95, Best practices ≥ 95)
- [ ] Deployment — Render / Fly.io for FastAPI + Vercel/Netlify for the SPA

When picking up the next task, always re-read **`DESIGN.md`** for the
ground-truth folder structure and conventions.
