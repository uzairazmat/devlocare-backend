# DESIGN.md — DevloCare Frontend Architecture

> **Frozen contract.** Single source of truth for the React SPA that consumes
> the FastAPI backend documented in `CLAUDE.md`. Every screen, hook, store,
> token, and folder follows what is written here. If a change is needed,
> update this file *first*, then the code.

---

## 0. North star

A **calm, clinical, GPT/Claude-inspired conversational UI** for patients
(plus a separate, dense admin console reachable from the same shell). It must
feel premium and trustworthy — health is sensitive — while running fast on
mid-tier laptops and Android phones, in both English and Urdu (RTL), with a
proper light/dark theme.

Design priorities, in order: **trust → clarity → speed → delight**.

---

## 1. Stack & rationale

| Concern | Choice | Why |
|---|---|---|
| Framework | React 18 + Vite 5 + TypeScript (strict) | FRD calls for React (Vite); SPA fits the chat metaphor; HMR is instant |
| Routing | React Router v6 (`createBrowserRouter`, lazy routes) | Code-split per screen; nested layouts |
| Styling | Tailwind CSS + CSS variable tokens | Token-driven; no hex in components |
| UI primitives | Radix UI (headless) wrapped in our own components | Accessibility for free, full visual control |
| Server state | TanStack Query v5 | Cache, retry, stale-while-revalidate, infinite, optimistic |
| Client state | Zustand (sliced, selective `persist`) | Minimal, DX-friendly, no provider hell |
| HTTP | Axios instance + JWT interceptor | One chokepoint for auth headers, error normalisation |
| Forms | React Hook Form + Zod resolver | Strict types, low re-renders, shared schemas with API |
| i18n | i18next + react-i18next | EN/UR JSON catalogs, lazy-loaded namespaces |
| Charts | Recharts (admin dashboards) + Chart.js (explainability bars) | Both required by the FRD |
| Virtualization | `@tanstack/react-virtual` | History, KB, consultations, feedback tables |
| Icons | `lucide-react` | Tree-shakeable, consistent stroke |
| Date | `date-fns` | Tiny, tree-shakeable, locale-aware |
| Animation | Framer Motion *for chat reveal & drawer only* | No "look at me" animations |
| Testing | Vitest + RTL + Playwright | Unit, integration, e2e |
| Quality | ESLint + Prettier + TypeScript strict + Husky + lint-staged | CI-gated |

---

## 2. Folder structure (production grade)

```
devlocare-frontend/
├─ public/
│  ├─ favicon.svg
│  └─ og-image.png
├─ src/
│  ├─ main.tsx                  ← bootstraps providers + router
│  ├─ App.tsx                   ← <RouterProvider />
│  │
│  ├─ app/                      ← composition root
│  │  ├─ router.tsx             ← createBrowserRouter, lazy routes, guards
│  │  ├─ providers/
│  │  │  ├─ AppProviders.tsx    ← chains all providers below
│  │  │  ├─ QueryProvider.tsx   ← TanStack Query client + Devtools (dev only)
│  │  │  ├─ ThemeProvider.tsx   ← reads useThemeStore, sets data-theme + dir
│  │  │  ├─ I18nProvider.tsx    ← initializes i18next from useLanguageStore
│  │  │  └─ AuthGate.tsx        ← bootstraps /auth/me on app load
│  │  └─ guards/
│  │     ├─ RequireAuth.tsx
│  │     ├─ RequireAdmin.tsx
│  │     └─ RequireSuperAdmin.tsx
│  │
│  ├─ config/
│  │  ├─ env.ts                 ← Zod-validated import.meta.env at boot
│  │  ├─ routes.ts              ← typed route paths (single source for links)
│  │  └─ constants.ts           ← non-secret, non-tunable defaults
│  │
│  ├─ lib/
│  │  ├─ api/
│  │  │  ├─ axios.ts            ← instance + interceptors
│  │  │  ├─ endpoints.ts        ← typed wrappers for every backend route
│  │  │  ├─ queryKeys.ts        ← centralized cache keys
│  │  │  └─ errors.ts           ← normalized ApiError + toast mapper
│  │  ├─ auth/
│  │  │  ├─ tokenStore.ts       ← in-memory + sessionStorage fallback
│  │  │  └─ jwt.ts              ← decode-only helpers (no signing client-side)
│  │  ├─ validation/
│  │  │  ├─ pii.ts              ← regex parity with backend
│  │  │  └─ schemas.ts          ← Zod schemas: auth, predict, feedback, admin
│  │  ├─ pdf/
│  │  │  └─ download.ts         ← blob-to-anchor helper for /history/export
│  │  ├─ i18n/
│  │  │  ├─ i18n.ts             ← init, fallback, namespace loader
│  │  │  └─ locales/
│  │  │     ├─ en/  (common.json, chat.json, admin.json, errors.json)
│  │  │     └─ ur/  (common.json, chat.json, admin.json, errors.json)
│  │  └─ utils/
│  │     ├─ cn.ts               ← clsx + tailwind-merge
│  │     ├─ format.ts           ← date, percent, confidence formatters
│  │     ├─ debounce.ts
│  │     └─ scroll.ts
│  │
│  ├─ stores/                   ← Zustand slices
│  │  ├─ index.ts
│  │  ├─ useAuthStore.ts        ← user, token, isAdmin, isSuperAdmin
│  │  ├─ useThemeStore.ts       ← 'light' | 'dark' | 'system' (persisted)
│  │  ├─ useLanguageStore.ts    ← 'English' | 'Urdu' (persisted)
│  │  ├─ useSidebarStore.ts     ← collapsed | expanded
│  │  ├─ useChatDraftStore.ts   ← draft text + meta (age, sex, severity, ...)
│  │  └─ useToastStore.ts       ← toast queue
│  │
│  ├─ hooks/
│  │  ├─ useDebouncedValue.ts
│  │  ├─ useAutoResizeTextarea.ts (uses useRef to grow composer)
│  │  ├─ useIntersectionObserver.ts (infinite history scroll)
│  │  ├─ useMediaQuery.ts
│  │  ├─ usePrefersReducedMotion.ts
│  │  ├─ useFocusOnMount.ts     (useRef + element.focus())
│  │  ├─ useEventListener.ts
│  │  └─ useShortcut.ts         (Cmd/Ctrl+K, Cmd+Enter)
│  │
│  ├─ types/
│  │  ├─ api.ts                 ← TS mirrors of Pydantic schemas
│  │  ├─ models.ts              ← domain types used in UI
│  │  └─ env.d.ts               ← ImportMetaEnv augmentation
│  │
│  ├─ components/
│  │  ├─ ui/                    ← primitive design system components
│  │  │  ├─ button.tsx
│  │  │  ├─ input.tsx
│  │  │  ├─ textarea.tsx
│  │  │  ├─ card.tsx
│  │  │  ├─ dialog.tsx
│  │  │  ├─ drawer.tsx
│  │  │  ├─ dropdown-menu.tsx
│  │  │  ├─ badge.tsx
│  │  │  ├─ pill.tsx            ← triage / specialist pills
│  │  │  ├─ tabs.tsx
│  │  │  ├─ tooltip.tsx
│  │  │  ├─ skeleton.tsx
│  │  │  ├─ progress.tsx
│  │  │  ├─ confidence-bar.tsx
│  │  │  ├─ rating-stars.tsx
│  │  │  ├─ empty-state.tsx
│  │  │  ├─ error-state.tsx
│  │  │  └─ toaster.tsx
│  │  ├─ layout/
│  │  │  ├─ AppShell.tsx        ← sidebar + topbar + outlet
│  │  │  ├─ Sidebar.tsx         ← history list / admin nav (role-aware)
│  │  │  ├─ Topbar.tsx          ← language toggle, theme, profile menu
│  │  │  ├─ MobileNav.tsx       ← drawer for ≤md breakpoints
│  │  │  └─ Disclaimer.tsx      ← persistent footer disclaimer
│  │  ├─ chat/
│  │  │  ├─ Composer.tsx        ← textarea + meta popover + submit
│  │  │  ├─ MetaPopover.tsx     ← age, sex, duration, severity, pregnancy, chronic
│  │  │  ├─ MessageList.tsx     ← virtualized message list
│  │  │  ├─ UserBubble.tsx
│  │  │  ├─ AssistantBubble.tsx
│  │  │  ├─ PredictionCard.tsx  ← top-3 + triage + specialist + tips
│  │  │  ├─ ExplanationPanel.tsx← rationale + key symptoms + Chart.js bars
│  │  │  ├─ RedFlagsCallout.tsx
│  │  │  └─ FeedbackBar.tsx     ← stars + optional comment
│  │  ├─ charts/
│  │  │  ├─ FeatureImportanceBar.tsx (Chart.js)
│  │  │  ├─ TopDiseasesBar.tsx        (Recharts)
│  │  │  ├─ TriageDonut.tsx           (Recharts)
│  │  │  └─ LanguageDonut.tsx         (Recharts)
│  │  └─ admin/
│  │     ├─ KpiCard.tsx
│  │     ├─ ConsultationRow.tsx
│  │     ├─ ConsultationDetailDrawer.tsx
│  │     ├─ FeedbackRow.tsx
│  │     ├─ KbRow.tsx
│  │     └─ KbEditor.tsx
│  │
│  ├─ features/                 ← screens + their queries/mutations
│  │  ├─ auth/
│  │  │  ├─ pages/
│  │  │  │  ├─ LoginPage.tsx
│  │  │  │  └─ RegisterPage.tsx
│  │  │  ├─ hooks/
│  │  │  │  ├─ useLogin.ts        ← useMutation
│  │  │  │  ├─ useRegister.ts
│  │  │  │  └─ useMe.ts           ← useQuery
│  │  │  └─ index.ts
│  │  ├─ chat/                  ← UC-01/03/04/05
│  │  │  ├─ pages/
│  │  │  │  └─ ChatPage.tsx       ← main consultation screen
│  │  │  └─ hooks/
│  │  │     └─ usePredictText.ts
│  │  ├─ history/               ← UC-06
│  │  │  ├─ pages/
│  │  │  │  └─ HistoryPage.tsx    ← detail view of one log_id
│  │  │  └─ hooks/
│  │  │     ├─ useHistoryInfinite.ts (useInfiniteQuery)
│  │  │     ├─ useDeleteLog.ts
│  │  │     └─ useExportPdf.ts
│  │  ├─ feedback/              ← UC-08
│  │  │  └─ hooks/useSubmitFeedback.ts
│  │  ├─ settings/
│  │  │  ├─ pages/SettingsPage.tsx
│  │  │  └─ hooks/useUpdateProfile.ts
│  │  └─ admin/
│  │     ├─ dashboard/pages/DashboardPage.tsx
│  │     ├─ consultations/pages/ConsultationsPage.tsx
│  │     ├─ feedback/pages/FeedbackPage.tsx
│  │     ├─ knowledge-base/
│  │     │  ├─ pages/KnowledgeBasePage.tsx
│  │     │  └─ hooks/useKbCrud.ts
│  │     └─ users/pages/UsersPage.tsx     ← super-admin only
│  │
│  └─ styles/
│     ├─ globals.css            ← Tailwind layers + base CSS
│     └─ tokens.css             ← :root + [data-theme="dark"] variables
├─ index.html
├─ vite.config.ts
├─ tailwind.config.ts
├─ postcss.config.js
├─ tsconfig.json
├─ tsconfig.node.json
├─ .eslintrc.cjs
├─ .prettierrc
├─ .env.example
├─ .gitignore
├─ package.json
└─ README.md
```

---

## 3. Environment & config

**Rule: nothing tunable is hardcoded.** Everything goes through
`import.meta.env.VITE_*`, parsed and validated by Zod at boot. If a required
var is missing or malformed, the app refuses to start with a clear error.

`.env.example`:
```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_APP_NAME=DevloCare
VITE_DEFAULT_LANGUAGE=English
VITE_ENABLE_VOICE=false          # gate UC-02 until STT is wired
VITE_ENABLE_DEVTOOLS=true        # React Query Devtools in dev only
VITE_TOKEN_STORAGE=session       # 'session' | 'memory'
VITE_SENTRY_DSN=                 # optional
VITE_BUILD_VERSION=dev
```

`src/config/env.ts` exports a typed `env` object so consumers do
`env.API_BASE_URL`, never `import.meta.env.VITE_API_BASE_URL` directly.

---

## 4. Routing & layouts

`createBrowserRouter` with a tree of nested routes; every screen is `lazy`.

```
/
├─ /  (public)              → marketing landing → CTA "Start consultation"
├─ /chat                    → ChatPage  (auth optional — guests work)
├─ /login                   → LoginPage
├─ /register                → RegisterPage
├─ /history                 → HistoryPage   (RequireAuth)
├─ /history/:logId          → HistoryDetailPage
├─ /settings                → SettingsPage  (RequireAuth)
└─ /admin                   → AdminShell    (RequireAdmin)
   ├─ /admin                → DashboardPage
   ├─ /admin/consultations  → ConsultationsPage
   ├─ /admin/consultations/:id → drawer-based detail (URL-driven)
   ├─ /admin/feedback       → FeedbackPage
   ├─ /admin/knowledge-base → KnowledgeBasePage
   └─ /admin/users          → UsersPage      (RequireSuperAdmin)
```

**Layouts.** A top-level `AppShell` renders the sidebar + topbar + an
`<Outlet />`. The shell adapts by role:
- patient: sidebar shows recent history + "New consultation" button
- admin: sidebar shows admin nav (Dashboard, Consultations, Feedback, KB,
  Users)

`AdminShell` is a nested layout that swaps the sidebar contents and adjusts
the topbar (back-to-app link). Both shells share the same theme + i18n
providers so language/theme is global.

**Route data**: heavy data is fetched via TanStack Query inside the page,
not via React Router loaders, so refocus refetch and cache-keying stay
uniform.

---

## 5. State management

### 5.1 Server state — TanStack Query

Single client, shared across the app. Sensible defaults:

```ts
defaultOptions: {
  queries: {
    retry: (failureCount, error) =>
      error instanceof ApiError && error.status === 401 ? false : failureCount < 2,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: 'always',
    refetchOnReconnect: 'always',
  },
  mutations: { retry: 0 },
}
```

Cache keys live in `src/lib/api/queryKeys.ts`:
```ts
export const qk = {
  me: ['auth', 'me'] as const,
  historyList: (limit: number) => ['history', 'list', { limit }] as const,
  historyItem: (logId: number) => ['history', 'item', logId] as const,
  predict: (hash: string) => ['predict', hash] as const,
  admin: {
    dashboard: ['admin', 'dashboard'] as const,
    consultations: (filters: ConsultationFilters) => ['admin', 'consultations', filters] as const,
    consultation: (id: number) => ['admin', 'consultations', id] as const,
    feedback: (page: number) => ['admin', 'feedback', page] as const,
    kb: (search?: string) => ['admin', 'kb', search ?? null] as const,
  },
};
```

**Patterns**
- History sidebar uses `useInfiniteQuery` keyed on `qk.historyList(limit)`
  with `getNextPageParam` reading `has_more` + `offset`.
- Submitting feedback **optimistically** updates the consultation cache
  (`setQueryData(qk.historyItem(logId), ...)`).
- Mutations that mutate server lists (delete log, KB CRUD, promote/demote)
  invalidate the relevant key on success.
- Predictions are **not** cached by default (each query is unique) — but
  if the user re-submits identical text+meta within 60s we reuse the
  response via a content-hash key (see `usePredictText`). Saves a round-trip.

### 5.2 Client state — Zustand

Thin slices, never one global store. Each slice:
- exposes a small surface (`useThemeStore((s) => s.theme)` style selectors),
- is **only** persisted when truly needed (theme + language).

Stores:
| Store | Persist | Notes |
|---|---|---|
| `useAuthStore` | no | Mirrors `/auth/me` for fast renders + role checks. JWT stays in `tokenStore`. |
| `useThemeStore` | yes (`localStorage`) | `'light' \| 'dark' \| 'system'`; ThemeProvider applies `data-theme` |
| `useLanguageStore` | yes | Drives i18next + `dir="rtl"` for Urdu |
| `useSidebarStore` | yes | `collapsed: boolean` |
| `useChatDraftStore` | no | Draft survives navigation but not refresh |
| `useToastStore` | no | Queue consumed by `<Toaster />` |

---

## 6. HTTP layer

`src/lib/api/axios.ts`:
- Base URL from `env.API_BASE_URL`.
- Request interceptor: attach `Authorization: Bearer <token>` from
  `tokenStore` if present.
- Response interceptor: on `401` → clear token, push `useAuthStore.logout()`,
  redirect to `/login` (preserving `from` for post-login bounce).
- Errors normalize to `ApiError { status, code, message, details }` so the
  toast mapper and React Hook Form can both consume them.

`src/lib/api/endpoints.ts` exports tiny typed functions per route:
```ts
export const api = {
  auth: {
    register: (b: RegisterBody) => http.post<TokenResponse>('/auth/register', b),
    login:    (b: LoginBody)    => http.post<TokenResponse>('/auth/login', b),
    me:       ()                => http.get<UserResponse>('/auth/me'),
    updateMe: (b: UpdateMeBody) => http.put<UserResponse>('/auth/me', b),
  },
  predict: { text: (b: PredictBody) => http.post<PredictionResponse>('/predict/text', b) },
  history: {
    list:   (q: { limit: number; offset: number }) => http.get<HistoryResponse>('/history', { params: q }),
    delete: (logId: number) => http.delete<MessageResponse>(`/history/${logId}`),
    export: (b: ExportBody) => http.post<Blob>('/history/export', b, { responseType: 'blob' }),
  },
  feedback: {
    submit:  (b: FeedbackBody) => http.post<FeedbackSubmitResponse>('/feedback', b),
    monitor: ()                => http.get<FeedbackMonitorResponse>('/feedback/monitor'),
  },
  admin: {
    dashboard:        ()                       => http.get<DashboardResponse>('/admin/dashboard'),
    consultations:    (q: ConsultationsQuery)  => http.get<AdminConsultationListResponse>('/admin/consultations', { params: q }),
    consultation:     (id: number)             => http.get<AdminConsultationDetail>(`/admin/consultations/${id}`),
    feedbackSummary:  ()                       => http.get<FeedbackAdminSummary>('/admin/feedback/summary'),
    feedbackList:     (q: PageQuery)           => http.get<FeedbackAdminListResponse>('/admin/feedback', { params: q }),
    kbList:           (q: KbQuery)             => http.get<DiseaseKBListResponse>('/admin/diseases', { params: q }),
    kbCreate:         (b: KbCreateBody)        => http.post<DiseaseKBItem>('/admin/diseases', b),
    kbUpdate:         (id: number, b: KbUpdateBody) => http.put<DiseaseKBItem>(`/admin/diseases/${id}`, b),
    kbDelete:         (id: number)             => http.delete<MessageResponse>(`/admin/diseases/${id}`),
    users: {
      createAdmin: (b: CreateAdminBody)            => http.post<UserResponse>('/admin/users/create-admin', b),
      promote:     (userId: number)                => http.post<UserResponse>(`/admin/users/${userId}/promote`),
      demote:      (userId: number)                => http.post<UserResponse>(`/admin/users/${userId}/demote`),
    },
  },
};
```

---

## 7. Themes (light + dark)

Tokens live in `src/styles/tokens.css`:
```css
:root {
  --bg-app: #F6FAF8; --bg-card: #FFFFFF; --bg-soft: #EAF4EF; --bg-hover: #F1F7F4;
  --border: #D7E5DE; --border-strong: #BDD3C8;
  --text-main: #091413; --text-heading: #163128; --text-body: #4B635B;
  --text-muted: #6E857D; --text-disabled: #9CB2AA;
  --primary: #285A48; --primary-hover: #1E4738;
  --secondary: #408A71; --accent-mint: #B0E4CC;
  --success: #16A34A; --warning: #D97706; --danger: #DC2626; --info: #0EA5A4;
}
[data-theme="dark"] {
  --bg-app: #091413; --bg-card: #13201D; --bg-soft: #1A2E28; --bg-hover: #20372F;
  --border: #254338; --border-strong: #35604F;
  --text-main: #E7FFF4; --text-heading: #C9EAD9; --text-body: #A8C5B7;
  --text-muted: #7FA291; --text-disabled: #5E786C;
  --primary: #408A71; --primary-hover: #4E9F84;
  --secondary: #5FA78C; --accent-mint: #B0E4CC;
  --success: #22C55E; --warning: #F59E0B; --danger: #F87171; --info: #2DD4BF;
}
```

Tailwind reads them through `tailwind.config.ts`:
```ts
colors: {
  app: 'var(--bg-app)',
  card: 'var(--bg-card)',
  soft: 'var(--bg-soft)',
  hover: 'var(--bg-hover)',
  border: { DEFAULT: 'var(--border)', strong: 'var(--border-strong)' },
  text: { main: 'var(--text-main)', heading: 'var(--text-heading)',
          body: 'var(--text-body)', muted: 'var(--text-muted)',
          disabled: 'var(--text-disabled)' },
  primary: { DEFAULT: 'var(--primary)', hover: 'var(--primary-hover)' },
  secondary: 'var(--secondary)', mint: 'var(--accent-mint)',
  success: 'var(--success)', warning: 'var(--warning)',
  danger: 'var(--danger)', info: 'var(--info)',
}
```

`ThemeProvider` reads `useThemeStore` (`'light' | 'dark' | 'system'`),
respects `prefers-color-scheme` for `'system'`, and writes `data-theme="dark"`
on `<html>`. It also writes `dir="rtl"` when the language is Urdu.

---

## 8. i18n & RTL

- `i18next` initialized in `lib/i18n/i18n.ts`. Two languages: `en`, `ur`.
- Namespaces: `common`, `chat`, `admin`, `errors`. Lazy-loaded per route.
- The `<html dir>` attribute is flipped by `ThemeProvider` based on language.
- All Tailwind utilities that depend on direction use logical equivalents
  (`ms-`, `me-`, `ps-`, `pe-`, `start-`, `end-`) so RTL "just works".
- API responses already include both `en` and `ur` for care tips and
  disclaimers — UI selects per `useLanguageStore`.

---

## 9. Performance & DOM optimization

1. **Code splitting.** Every route is `lazy()`. Admin chunk is separate from
   patient chunk; super-admin user mgmt is its own sub-chunk.
2. **Bundle hygiene.** `vite-plugin-visualizer` in `analyze` script. Target:
   patient first-load JS ≤ 180 KB gzipped.
3. **Tree-shaking.** Always named imports for `lucide-react`,
   `date-fns`, `recharts`, etc.
4. **Virtualized lists** with `@tanstack/react-virtual` on:
   - History sidebar (`useInfiniteQuery` + virtualizer)
   - Admin consultations table
   - Admin feedback list
   - Admin KB table
5. **Memoization rules.**
   - Row components in virtualized lists wrapped in `React.memo` with a
     custom `areEqual` (compare by `log_id`/`disease_id`).
   - `useMemo` for derived data (e.g., grouping history by date).
   - `useCallback` for stable handlers passed into memoized rows.
   - Selectors in Zustand always pluck a single primitive to avoid extra
     re-renders.
6. **`useRef` patterns.**
   - Composer `<textarea>` ref for autofocus + auto-resize.
   - Scroll container ref for "scroll to bottom on new prediction" without
     re-rendering parents.
   - Debounce timers (`useRef<number>()`).
   - Audio recorder handle (UC-02): `MediaRecorder` instance lives in a ref.
   - Intersection observer node ref for infinite history.
7. **Image / asset hygiene.** SVG icons inline; brand image as
   `<img loading="lazy" decoding="async">`. No webfonts beyond Inter
   (variable font, swap).
8. **Debounce / throttle.** Search inputs (KB, consultations) debounced
   300 ms; window resize listeners throttled with `rAF`.
9. **`startTransition`.** Sidebar filters and tab switches wrap state
   updates that re-render large trees in `startTransition` to keep input
   responsive.
10. **Avoid layout thrash.** Skeletons share dimensions with their loaded
    content so the page doesn't jump. The composer uses a `min-h` so
    auto-resize doesn't push the message list during typing.

---

## 10. Caching strategy

| Layer | Tool | Purpose |
|---|---|---|
| Server cache | TanStack Query | Stale-while-revalidate, retry, dedupe |
| HTTP cache | Axios + browser | `ETag`/`Last-Modified` honored if backend adds them |
| Asset cache | Vite hashed filenames | Long-lived `Cache-Control` on `/assets/*` |
| Local prefs | `localStorage` (Zustand `persist`) | theme, language, sidebar collapsed |
| Draft text | in-memory Zustand | survives navigation, not refresh |
| Service worker | optional Workbox preset | offline shell + cached static assets only — never cache API responses |

---

## 11. Forms & validation

- Schemas in `lib/validation/schemas.ts` (Zod). Client and server share the
  *intent* of the validation; the client copy mirrors the backend Pydantic
  rules.
- `react-hook-form` with `zodResolver(...)`.
- The PII regex from `app/models/request.py` is replicated in
  `lib/validation/pii.ts` so users see the error inline before the request
  goes out.
- Errors map: server `ApiError.code` → user-friendly i18n key in
  `errors.json`. Unknown codes fall back to a generic message.

---

## 12. Security

- **No JWT in `localStorage`.** Default storage is in-memory; refresh keeps
  user logged in via `sessionStorage` only if `VITE_TOKEN_STORAGE=session`.
  Roadmap: switch to httpOnly cookie + CSRF token when backend adds it.
- **Decode-only JWT.** We read the `exp`/`is_admin`/`is_super_admin` claims
  to short-circuit role checks; ground truth is always `/auth/me`.
- **Route guards** never trust client claims alone; protected pages also
  call `/auth/me` and let the API reject with 401/403 if mismatched.
- **No PII in URLs.** History uses opaque integer `log_id`, never user-
  generated content.
- **Disclaimer on every prediction render**, in user's language, with a
  visible link to /about/safety.
- **Self-harm content guard.** If `extracted_symptoms` or `raw_text` matches
  a small client-side keyword list (`suicide`, `kill myself`, etc., plus
  Urdu translations), we *additionally* show an emergency-resources card
  alongside the API response — never replacing it.
- **CSP.** Strict CSP set via `vite-plugin-html`: `default-src 'self'`,
  `connect-src 'self' VITE_API_BASE_URL`, no inline scripts (we ship
  hashed bundles).
- **Errors never leak stack traces** to the user; logged to console in dev
  and to Sentry in prod (if `VITE_SENTRY_DSN` is set).

---

## 13. Module-by-module screen plan

### 13.1 Auth (`/login`, `/register`)
- Hooks: `useLogin`, `useRegister` — both `useMutation`.
- On success: `tokenStore.set(...)` → `useAuthStore.setUser(...)` →
  invalidate `qk.me` → navigate to `from` or `/chat`.
- Register form fields: `username`, `email`, `password`, optional `age`,
  `sex`, `language_pref`. PII regex blocks pasting bad data.
- Inline error mapping: `email_taken`, `username_taken`, `weak_password`,
  validation arrays from FastAPI `RequestValidationError`.

### 13.2 Chat — UC-01/03/04/05 (`/chat`)
- **Composer** at the bottom: auto-resizing `<textarea>`, meta popover for
  age/sex/duration/severity/pregnancy/chronic, language toggle, voice
  button (disabled unless `VITE_ENABLE_VOICE=true`), Submit (Cmd+Enter).
- **Conversation log** above: user bubble (right, primary fill) + assistant
  bubble (left, soft surface) + a **`PredictionCard`** which expands to:
  - Top-3 conditions as horizontal cards, each with a `ConfidenceBar` and
    triage `Pill` (`self_care` mint / `see_gp` amber / `urgent_care` red).
  - `RecommendedSpecialist` row.
  - `CareTips` block, language-aware.
  - `RedFlagsCallout` (only if non-empty) with warning state styling.
  - `ExplanationPanel`: rationale paragraph + key symptoms chips +
    Chart.js horizontal bar of `feature_importance`.
  - `FeedbackBar` once a `log_id` is present (i.e. user is logged in).
- Empty state: a thoughtful "How can I help?" with example chips you can
  click to fill the composer.
- **Streaming-style reveal** for the assistant bubble: appears immediately
  with a mint-pulse skeleton, swaps to content when the request resolves.
- A persistent **medical disclaimer** chip is pinned above the composer.

### 13.3 History — UC-06 (`/history`, `/history/:logId`)
- Sidebar: `useInfiniteQuery` for paginated list, virtualized with
  `react-virtual`. Each row shows a 140-char preview + main condition pill
  + relative time.
- Selecting a row routes to `/history/:logId` and renders the full
  `PredictionCard` from cached or refetched data.
- Toolbar: bulk select → `useExportPdf` (calls `/history/export` with
  `log_ids`), or `Export last 10` button (uses `last_n: 10`). Result blob
  is saved with `lib/pdf/download.ts`.
- Per-row delete → confirm dialog → `useDeleteLog` (optimistic remove).
- Empty (guest): friendly login prompt card.

### 13.4 Settings (`/settings`)
- Profile form: `age`, `sex`, `language_pref`. `useUpdateProfile`.
- Theme picker (`light`/`dark`/`system`).
- Language toggle (also accessible from topbar; this is the canonical
  location).
- Danger zone: "Delete all my history" — multi-step confirmation; calls
  delete in batches.

### 13.5 Admin — Dashboard (`/admin`)
- Single `useQuery` against `/admin/dashboard`.
- KPI cards: total, today, urgent, avg rating, total feedback.
- `TopDiseasesBar`, `TriageDonut`, `LanguageDonut`, all responsive.
- "Refresh" button manually invalidates `qk.admin.dashboard`.

### 13.6 Admin — Consultations (`/admin/consultations`)
- Filter bar: free-text disease search (debounced), triage select, language
  select. Filters live in the URL (`useSearchParams`) so links are
  shareable and back-button-friendly.
- Virtualized table; each row is `React.memo`.
- Click row → drawer (`/admin/consultations/:id`) with full detail —
  rendered as `PredictionCard` reused from chat, plus an admin-only
  "internal notes / SHAP raw" tab.

### 13.7 Admin — Feedback (`/admin/feedback`)
- Top: summary cards from `/admin/feedback/summary`.
- Below: virtualized list from `/admin/feedback` (paginated).
- Click feedback row → drills into the source consultation drawer.

### 13.8 Admin — Knowledge Base (`/admin/diseases`)
- Search input (debounced) → `useQuery(qk.admin.kb(search))`.
- Virtualized table.
- "Add disease" button → `KbEditor` dialog with bilingual fields.
- Inline edit / delete with optimistic cache updates and rollback on error.

### 13.9 Admin — Users (`/admin/users`, super-admin only)
- Form to create new admin (`/admin/users/create-admin`).
- List of users (future endpoint, currently we only have promote/demote
  by id, so the page also provides a "promote by user_id" form).
- Confirm-twice for promote/demote.

---

## 14. Component catalogue (rules)

- All primitives in `components/ui/*` consume design tokens via Tailwind
  classes; **no raw hex anywhere**.
- All interactive elements have:
  - visible focus ring (`focus-visible:ring-2 ring-primary`)
  - keyboard support
  - `aria-*` attributes via Radix where relevant
- `Button` variants: `primary`, `secondary`, `ghost`, `danger`,
  `link`. Sizes: `sm`, `md`, `lg`, `icon`.
- `Pill` is the only place we render triage colors; it accepts
  `level: TriageLevel` and renders the right semantic color.
- Skeletons match real heights so layouts don't jump.

---

## 15. Accessibility checklist (PR-blocker)

- All form fields have associated `<label>`.
- Color contrast verified against WCAG AA (CI script using `axe-core`).
- Keyboard-only flows tested: register → predict → save → export.
- `aria-live="polite"` on the assistant bubble container so screen readers
  announce new predictions.
- Reduced motion respected — animations disabled when
  `prefers-reduced-motion: reduce`.
- Skip-to-main-content link at the top of `AppShell`.

---

## 16. Testing strategy

- **Unit (Vitest + RTL).** Components, hooks, validators, formatters,
  store reducers. ≥ 80% line coverage on `src/lib/**`.
- **Integration.** Each feature page rendered with a mock TanStack Query
  client + MSW (Mock Service Worker) handlers mirroring the Pydantic
  shapes. Critical flows: login, predict, save log, submit feedback,
  delete history, export PDF.
- **e2e (Playwright).** Three scripts:
  1. Patient happy path: register → predict → see prediction →
     submit feedback → export PDF.
  2. Admin happy path: super-admin creates an admin → admin logs in →
     dashboard renders → KB CRUD round-trip.
  3. RTL/Urdu: switch language, verify `dir="rtl"`, verify Urdu strings
     present on the result page.
- **Lighthouse CI** against `npm run preview`; thresholds: Performance 90,
  A11y 95, Best Practices 95, SEO 90.

---

## 17. Build, deploy, observability

- `npm run build` outputs to `dist/`; Vite emits hashed assets.
- Static hosting (Vercel / Netlify / Cloudflare Pages). SPA fallback to
  `index.html`.
- Backend on Render / Fly.io / a tiny VPS; CORS configured to the SPA
  origin. Set `VITE_API_BASE_URL` per environment.
- Sentry (optional) via `VITE_SENTRY_DSN`. Source maps uploaded on build.
- Health check: SPA `/` and API `/api/v1/health` both monitored.

---

## 18. Definition of done (per screen)

A screen is "done" when:
1. Loading + success + empty + error states all render without layout jumps.
2. It has at least one Vitest + RTL test for its happy path.
3. It localizes every visible string.
4. It works in dark mode and RTL.
5. Lighthouse a11y ≥ 95 for that page.
6. It re-renders ≤ 5 times when its primary data updates (verified via
   React DevTools profiler).
7. It does not import anything from `process.env` or hardcode a URL.

---

> When in doubt, optimize for the patient's confidence and the clinician's
> trust. Ship narrow, deep, tested.
