# Handoff

Rewritten 2026-09-18 at `39d9ce44`, replacing a HANDOFF.md that tracked an unrelated effort (the
classroom simulation) — that tracking is not preserved here; see that thread's own session if it's
needed. **`CLAUDE.md` is what is kept current** — when a step below lands, move its rule into
CLAUDE.md and delete the step here.

**Scope: the security hardening plan only.** Plan file:
`~/.claude/plans/create-a-plan-to-mossy-cupcake.md`, with two addenda already folded into it
(2026-08-29 and 2026-09-02) recording what earlier PR review found already fixed or already built.
Nothing in this document has been implemented by writing code in this session — every "DONE" item
below was **found already true** by re-reading the live repo, not built to satisfy this plan. Every
other item is **NOT STARTED**: this plan has produced research and this file, and no PR yet.

## Suite counts at this head

**No suite has been run in this session** — no code has changed, so there is nothing to measure yet.
When implementation starts, the first canary line goes here per CLAUDE.md's rule (`git status
--short` + `git log --oneline -1` for the first two fields, a real run for the three suite counts —
never carried forward from a different branch or invented).

Working tree at rewrite time: `dialog-focus-restore` @ `39d9ce44`, one unrelated dirty file
(`frontend/src/pages/teacher/Questions.test.jsx`, untouched by this work — not explained by anything
in this thread, so don't build on it; if it's still there when implementation starts, resolve it
first per CLAUDE.md's canary rule rather than committing around it).

---

# Why this plan exists

AdaptiveLearning handles children's biometric data (EEG, heart rate, facial/emotion) and academic
records. A security review (this plan) audited the whole stack — transport, network edge, auth,
sessions, authorization, input validation, the LLM surface, data minimization, rate limiting,
logging, dependencies, frontend — against a checklist the user supplied (drawn from a security-tips
video) plus direct inspection of the live repo. Two decisions were made explicit up front and hold
throughout: **bearer-token auth is kept** (no migration to cookies, so CSRF tokens are out of scope
by construction), and **the admin role/panel already exists** (`_role()`, `_require_admin`,
`feature_flags` — PR #125), so every admin-gated piece below builds directly on it rather than on
a hypothetical future panel.

The plan was written, then re-verified twice against a fast-moving `main` (2026-08-29 and
2026-09-02) because two of its headline findings turned out to already be fixed by unrelated work
that landed while the plan was being written. **Re-verify line numbers and any "already fixed" claim
below against current `main` before writing code from this document** — `LLM_topic_decider.py` in
particular has moved three times already (365→607→798) as new topics were added, and nothing here
promises the next line number is still current.

---

# §5 Authorization — role-gate bug. DONE (found already fixed, not fixed here).

**The finding**: `create_class`, `my_classes`, and `link_child` read `user["user_metadata"]["role"]`
— a field the client can rewrite via `supabase.auth.updateUser({data:{role:'teacher'}})`, bypassing
the backend entirely. A student could self-elevate to pass these three checks.

**Status, re-verified 2026-09-18**: fixed. All three now call `_role(user["id"])`, which reads
`profiles.role` (UPDATE/INSERT revoked from `anon`/`authenticated` by
`20260824010000_profiles_role_is_not_client_writable.sql`, fails closed to `student` on a read
error). Traced to PR #125 — the same PR that built `_require_admin`. This was **not** done in
response to this plan; the plan found it already true on its second review pass.

**The regression test this plan asked for also already exists**:
`Website/AdaptiveLearning/backend/tests/test_role_gates.py` contains
`test_no_endpoint_gates_on_user_metadata`, which greps the module so a fourth site reading
`user_metadata.role` fails CI. Confirmed present 2026-09-18. **Nothing left to do in §5.**

---

# §7 AI/LLM — usage caps. DONE (found already fixed, not fixed here).

**The finding**: only `/api/students/{id}/learning-strategies` had a rate limiter
(`_rate_limit_strategies`); question generation — the hottest path in the product — had no per-call
timeout, no concurrency bound, no daily spend ceiling, and no bound on callers queued waiting for a
slot.

**Status, re-verified 2026-09-18**: fully built. `Website/AdaptiveLearning/backend/llm_client.py`
(new since the plan's original research pass, built in PR #145 "One dispatch point for both model
providers, and four bounds around generation") bounds every generation call:
`GENERATION_LLM_TIMEOUT` (30s), `GENERATION_MAX_CONCURRENCY` (8, a process-wide `BoundedSemaphore`),
`GENERATION_DAILY_CALL_LIMIT` (2500/24h, Claude-only), `GENERATION_MAX_WAITERS` (30, load-tested —
see CLAUDE.md's *Every model call goes through `llm_client`* section for the arrival-rate numbers
behind that figure). This predates and is unrelated to this plan; PR #145 was building the
Ollama→Claude migration (`~/.claude/plans/create-the-plan-to-delegated-prism.md`, itself now fully
shipped and a historical record, not a to-do). **Nothing left to do in this part of §7.**

---

# §7 AI/LLM — prompt injection. NOT STARTED. Still open, and still the plan's sharpest concrete finding.

`LLM_topic_decider.py` interpolates the raw `grade` string, unescaped and unbounded, directly into
an f-string prompt that drives topic/difficulty selection:

```python
Student Grade Level = {grade}
```

**Re-verified 2026-09-18 at line 798** (moved again from the plan's last-recorded `:607`, as more
topics — `spread`, the grade-9 solvers — were added; re-check the line before editing, this file is
still under active churn). `grade` traces back to `CreateClassRequest.grade_level` /
`UpdateClassRequest.grade_level` / `UpdateProfileRequest.grade_level`, all still declared as
unconstrained `str | None` with no `max_length`, no allowed-value check, and no DB CHECK constraint
— confirmed unchanged.

**Why this is real and not theoretical**: `LLM_topic_decider._allowed_topics()` (added by PR #134,
unrelated to this plan) numerically *parses* `grade` for topic-gating logic via
`grade_levels.grade_number()` — but that parsed, validated number is a **separate value** from the
raw string that still reaches the prompt. Fixing the topic-selection gate did not fix the prompt
interpolation; they're two different consumers of the same field.

**Fix, two layers, neither built**:
1. **Primary**: constrain `grade_level` at the Pydantic layer — a `Literal[...]` or regex-anchored
   short value (this codebase already has a real, small, closed vocabulary to draw from — the
   frontend dropdown's own strings). A correctly constrained value can't carry an injection payload.
2. **Defense-in-depth**: a shared `_prompt_safe(s, max_len)` helper — strip prompt-control
   sequences/delimiters, hard-truncate regardless of the Pydantic cap — reused across
   `LLM_topic_decider.py` and every `LLM_*_generation.py` file, so a future field reusing this
   prompt path without the same constraint is still covered.
3. **Follow-up, not yet traced**: audit whether `get_user_performance`/`get_user_history` ever let
   free text (an answer string, a display name) reach these prompts. The aggregates checked so far
   are numeric/DB-sourced and lower risk, but this wasn't exhaustively verified.

---

# Everything else in the plan. NOT STARTED.

Re-verified against current `main` (`39d9ce44`, 2026-09-18) one section at a time — nothing below has
moved since the plan was written; each bullet states what was just re-confirmed absent.

## §1 Transport — HSTS. NOT STARTED.

No `Strict-Transport-Security` header anywhere. Plan: add it via the §2 middleware, env-gated
(`ENABLE_HSTS`, off in local dev). **Do not** add an app-level HTTPS redirect — no production
hosting/reverse-proxy config exists in this repo yet, so whether TLS terminates at a proxy in front
of FastAPI is an **open decision** (see Open Decisions below); guessing wrong here creates a redirect
loop. Defer HSTS `preload` list submission (irreversible) until the production domain is final.

## §2 Network Edge — CORS, security headers, `/docs`, request size. NOT STARTED.

Re-verified 2026-09-18: `main.py:107` is still `allow_origins=["*"]` with `allow_credentials=True`.
Zero security-headers middleware anywhere (grepped for `SecurityHeadersMiddleware`,
`X-Frame-Options`, `Content-Security-Policy` — no matches). `FastAPI(title=...)` still has no
`docs_url`/`redoc_url`/`openapi_url` override, so `/docs`, `/redoc`, `/openapi.json` are public. No
request-body-size middleware exists.

Plan, unchanged from the document:
- CORS → explicit `ALLOWED_ORIGINS` env allowlist, `allow_credentials=False` (bearer-in-header
  doesn't need cookie credentials), narrowed methods/headers. Mirror
  `EEGResearch/src/app/main.py:38-39`'s existing pattern rather than inventing a new one.
- `SecurityHeadersMiddleware`: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`,
  `Permissions-Policy: camera=(self), microphone=(), geolocation=(), payment=()` (the `camera=(self)`
  carve-out is deliberate — the app legitimately uses webcam access), and a CSP
  (`default-src 'self'; connect-src 'self' <supabase-url> <eeg-sidecar-origin>; ...`) — **first
  confirm whether `main.py` ever serves HTML or is JSON-only behind the separately-hosted Vite
  frontend**; if JSON-only, the real CSP target is the frontend's `index.html` / hosting-platform
  config, an open decision pending the hosting choice.
- `docs_url=None, redoc_url=None, openapi_url=None` when `ENV=production`; keep enabled locally/CI.
- `MaxBodySizeMiddleware`, ~256KB cap on normal JSON endpoints (the ingest endpoints already cap
  batch *count* via `_INGEST_MAX_BATCH`, not raw byte size within a valid count).
- Directory listing: N/A today (no `StaticFiles` mount) — document as forward-looking policy in
  CLAUDE.md for if one is ever added.

## §3 Authentication. NOT STARTED.

Re-verified `supabase/config.toml` 2026-09-18: `[auth.captcha]` still fully commented out;
`secure_password_change = false` still; `otp_expiry = 3600` still; `[auth.mfa.totp]` and
`[auth.mfa.phone]` both still `enroll_enabled = false` / `verify_enabled = false`.

**Architectural constant that shapes this whole section**: login never touches the backend
(`AuthContext.jsx` calls `supabase.auth.signUp`/`signInWithPassword` directly), so login rate
limiting, account lockout, and CAPTCHA can only be configured in Supabase — Dashboard or Auth
Hooks — never in `main.py`.

Not started:
- **Verify `config.toml` vs. hosted-project Dashboard authority** before relying on any of the
  below (see Open Decisions #3) — `config.toml` is the local-CLI config and may not mirror the
  hosted project's real settings.
- Enable `[auth.captcha]` (hCaptcha or Turnstile) on sign-in/sign-up/reset forms — the concrete
  answer to "bot protection."
- True account lockout needs a Postgres Auth Hook (design: a `login_attempts` table + an
  `_require_admin`-gated `POST /api/admin/accounts/{id}/unlock` endpoint, wired into the existing
  admin panel the way `SchoolYear.jsx` wraps `/api/admin/retention-window`) — **blocked on
  confirming the Supabase plan tier supports Auth Hooks** (Open Decisions #2).
- User enumeration: audit `Register.jsx`/`Login.jsx`/reset-flow copy for branching that reveals
  account existence; genericize regardless of the underlying Supabase response. Not yet audited.
- 2FA/OTP (teachers/parents: TOTP or email-OTP; students: email-OTP only on an unrecognized device):
  - Enable native TOTP MFA in Supabase, add an enrollment-enforcement guard for `teacher`/`parent`
    roles (Supabase's own MFA is opt-in per-user by default; enforcing it per-role is app-level work
    not yet designed in detail).
  - Student "remember this device" needs a signed device token minted after a verified OTP
    challenge — no native Supabase primitive, needs an Edge Function or Auth Hook. **Flagged as the
    single biggest open architectural question in the whole plan** — blocked on the same Auth Hook
    availability question as account lockout.
  - **Toggle — designed, not built**: no new table needed. Add `"mfa_enabled": False` (plus a
    per-role scoping value if needed) to `main.py`'s `_FEATURE_FLAG_DEFAULTS` dict — it's then live
    in the existing admin panel's feature-flags page through `/api/admin/flags/{key}`, with the
    existing `feature_flag_changes` audit trail, no migration.

## §4 Session Management. NOT STARTED.

- Flip `config.toml`'s `secure_password_change` to `true` (still `false`) — low-risk, one-line,
  invalidates other sessions on a password change. Verify it's mirrored at the hosted Dashboard.
- Shorten `otp_expiry` (still `3600`) to 900–1800s for reset links; test against the `email_sent=2/hr`
  resend limit so a too-short window doesn't lock legitimate users out of their own reset flow.
- Make `frontend/src/lib/supabase.js:10`'s implicit `createClient` auth options explicit
  (`autoRefreshToken: true, persistSession: true, detectSessionInUrl: true`) so the security-relevant
  choice is visible in code, not implicit defaults a future edit could silently break. Confirm
  refresh-token rotation is genuinely enabled at the Dashboard, not just assumed from the SDK
  default.
- Secure cookie flags: N/A today (no cookies in this app's own code) — noted for if the §3
  "remember this device" token ends up implemented as a cookie (`HttpOnly; Secure; SameSite=Strict`
  if so).
- CSRF: deliberately out of scope, not a gap — bearer-in-header is not an ambient credential, so a
  forged cross-site request can't carry a valid token without first reading it out of `localStorage`
  (an XSS problem the CSP/ESLint work below addresses, not a CSRF one).

## §5 Authorization — mass-assignment audit. NOT STARTED.

None of `main.py`'s request models (`CreateClassRequest`, `UpdateClassRequest`,
`UpdateProfileRequest`, etc.) declare `model_config = ConfigDict(extra='forbid')` — confirmed
2026-09-18, zero matches repo-wide. Not a live bypass today (Pydantic v2 silently drops unexpected
fields rather than leaking them through), but add it to every request model for defense-in-depth and
clearer 422s. Specifically verify `UpdateProfileRequest`'s handler builds its update dict from named,
validated attributes only, never `payload.model_dump()` wholesale — not yet checked.

## §6 Input Validation & Injection Defense. NOT STARTED (except confirmed-solid invariants).

- **Already solid, no action**: zero raw/string-built SQL anywhere (100% through the Supabase client
  or RPC) — add a one-line CI grep-lint so this stays enforced as the codebase grows, not yet added.
  SVG/HTML escaping in `chart_render.py` already runs every interpolation through `html.escape()` —
  add a regression test asserting this stays true, not yet added. React/frontend XSS-sink usage
  confirmed still zero (`dangerouslySetInnerHTML`/`eval`/`Function()`/`innerHTML=`/`document.write`)
  — add ESLint rules (`react/no-danger` + a custom ban) to `frontend-build` so this stays enforced,
  not yet added.
- **Not started**: free-text field caps. `display_name`, class `name`, and `grade_level` (see the
  §7 prompt-injection section above — same root fields) are all unconstrained. Add
  `Field(max_length=100)` to `display_name`/class `name`; give `grade_level` the strict
  `Literal`/regex treatment described in §7, since it has a confirmed LLM-prompt consumer.
- File uploads: N/A, confirmed no upload feature exists anywhere in the app. Document the
  forward-looking policy (content-sniffed MIME validation, hard size cap, private-bucket storage
  matching the existing `session-charts` pattern, image re-encoding) in CLAUDE.md for whenever one
  is added — not yet documented there.

## §8 Data Protection & Minimization. NOT STARTED (partly already solid).

- **Already solid, no action**: Postgres-managed encryption at rest/in transit; RLS extensively
  enabled and CI-asserted; the leaderboard endpoint (`main.py`) is a good precedent for stripping
  identifiers and clamping caller-supplied limits.
- **Not started**: extend that pattern — audit other response-shaping points for an unrendered
  identifier or an unclamped limit/pagination param. Specifically flagged and not yet checked: the
  class-roster endpoint's `email` field (appropriate for a teacher on their own class — verify no
  other caller path reaches it), and any endpoint returning `_profile()`'s full dict to a viewer who
  only needs `display_name`.
- App-level encryption for EEG/facial columns: recommended as a **follow-on discussion**, not part
  of this implementation pass — see Open Decisions #4, the aggregation-query trade-off is real.

## §9 Rate Limiting & Bot/Abuse Defense. NOT STARTED.

Both existing limiters (`_rate_limit_strategies`, `_rate_limit_ingest`) key exclusively on
authenticated `user_id`. Audit `main.py` for any route reachable without calling `get_user(request)`
first (health checks, any public read endpoint) — those have zero rate protection today. Not yet
audited. Mindful of `X-Forwarded-For` trust once behind a proxy (same open question as §1).

## §10 Logging & Monitoring. NOT STARTED.

Confirmed still totally absent: no security-event table, no failed-login counter, no
`audit_log`/`security_log` anywhere. Plan: new `security_events` table (same
REVOKE/RLS-on-no-policies/service-role-only pattern as `retention_window`; `feature_flag_changes` is
the closer append-only precedent to follow directly), populated from existing natural hook points
(authz denials from the `_verify_*` helpers, consent changes at `_consent_actor` call sites,
rate-limit 429s, eventually failed-login events once the §3 Auth Hook exists). An
`_require_admin`-gated `GET /api/admin/security-events` endpoint, added alongside the existing
`/api/admin/*` block, with a real admin-panel page — not a placeholder. **Explicit non-goal, worth
restating**: never log raw biometric values or full request bodies — only that an event happened.

## §11 Dependency & Supply-Chain. NOT STARTED.

Re-verified 2026-09-18: CI still runs exactly the same six jobs it always has (`eegresearch-tests`,
`native-bridge-build`, `website-backend`, `database-grants`, `database-migrations`,
`frontend-build`) — no `dependency-scan` job, no `.github/dependabot.yml`, no gitleaks/trufflehog job
anywhere in `.github/workflows/`. Plan: add a `dependency-scan` job (`pip-audit` for both the website
backend and `EEGResearch` — separate requirement sets — plus `npm audit --audit-level=high` for the
frontend), a root `dependabot.yml` (pip × 2, npm, weekly), and a `gitleaks` (or `trufflehog`) job on
every PR/push. Nothing here touches already-solid ground: `.env` stays gitignored, no secrets are
in-repo today, and the database-grants discipline (`check_function_grants.py`/`check_table_grants.py`)
is mature and already CI-enforced — this section only adds the two missing scanners.

## §12 Frontend Hardening. NOT STARTED (mostly covered by §2/§6 above).

No separate work beyond what §2 (CSP) and §6 (ESLint rules, token-lifetime shortening from §4)
already cover. The one residual risk — the Supabase JWT sitting in `localStorage`, readable by any
same-origin JS if an XSS vector ever appeared — is real but theoretical today (zero XSS-sink usage
confirmed), and CSP + the ESLint guardrails are the whole mitigation. No migration to httpOnly
cookies — a deliberate, already-made decision, not an oversight.

---

# Open decisions — need a human call before implementing, not just more research

1. **Hosting/reverse-proxy choice.** Gates the HTTPS-redirect design (§1) and where CSP is best
   applied on the frontend (§2). No production hosting config exists in this repo yet.
2. **Supabase plan tier.** Gates whether Auth Hooks are available at all, which gates true account
   lockout and the student "remember this device" OTP-skip mechanism (§3) — the single biggest
   architectural open question in the plan. Needs checking before any further design effort there.
3. **`config.toml` vs. hosted Dashboard authority.** Several §3/§4 fixes assume whichever is
   authoritative for the real project gets edited. Not yet verified which one actually is.
4. **App-level encryption for EEG/facial columns (§8).** Recommended as a scoped follow-on
   discussion given the aggregation-query trade-off, not bundled into this implementation pass.

---

# Suggested implementation order

Not prescribed by the plan itself, but a reasonable sequence given what's already confirmed:

1. **§7 prompt injection** — the sharpest concrete, still-open finding; small, self-contained
   (`grade_level` field constraint + the shared `_prompt_safe` helper).
2. **§2 CORS/headers/`/docs`/request-size** — all backend-only, no external dependency, no open
   decision blocking any of it except the CSP target question (which can ship a backend-side CSP
   now and revisit the frontend placement once hosting is chosen).
3. **§6 free-text caps + §5 `extra='forbid'`** — small, mechanical, same PR as §7 makes sense given
   the shared fields.
4. **§11 dependency/secret scanning** — pure CI addition, no app-code risk, no open decision blocking
   it.
5. **§10 security-events logging** — needs the admin panel's existing patterns but no external
   dependency.
6. **§1 HSTS header** (not the redirect) — can ship now; the redirect waits on Open Decision #1.
7. **§3/§4 Supabase-config changes** (`secure_password_change`, `otp_expiry`, CAPTCHA) — resolve
   Open Decision #3 first, then these are dashboard/config edits, not code.
8. **§3 MFA + account lockout** — the largest remaining piece, blocked on Open Decision #2 (Auth
   Hook availability) before it can be scoped further.
9. **§8 App-level encryption** — explicitly deferred to its own follow-on discussion.

---

# Noticed in passing, out of this plan's scope: legal/compliance gaps

**Not legal advice — flagged from a code/repo audit, not a lawyer's review.** This product records
children's EEG and facial data inside a school (teacher/class/school-year) structure, which is
about as high-stakes a data-handling context as exists; get real counsel (ed-tech / student-privacy
/ biometric-privacy experience) before launch. None of this is a section of this plan and nothing
above touches it — it's legal/compliance work, not a code-security control — but it's dropped here
rather than silently, the same as the privacy-policy finding it started from.

- **No privacy policy exists anywhere.** Checked 2026-09-18: no route in the frontend, no doc in the
  repo root or `supabase/`, no copy referencing one. Confirmed by grep (`privacy.?policy`,
  case-insensitive, whole repo) and by glob (`**/*rivacy*`) — the only hits are `Privacy.md` inside
  the third-party `onnxruntime` package, unrelated. The app already has a real, code-enforced
  consent system (`signal_consent`, per-channel, parent-vs-student write rules, an erasure endpoint)
  with consent-screen copy in the frontend, but nothing that functions as the policy document that
  copy would normally point back to.
- **The consent mechanism is real but likely isn't COPPA's "verifiable parental consent."**
  `ConsentChannels.jsx` lets a linked parent toggle EEG/heart/camera on or off — genuine,
  code-enforced, revocable consent, and good engineering. But COPPA's verifiable-parental-consent
  bar for collecting data from children under 13 is a specific, narrow list of accepted methods
  (signed form, credit-card transaction, phone/video verification, government ID check, etc.); an
  in-app toggle by whoever holds the parent login doesn't verify that person is actually the parent.
  This is the highest-risk single gap given the data type (biometric + under-13 children).
- **State biometric-privacy laws (Illinois BIPA and similar) are a specific, real exposure.** These
  often carry a private right of action with statutory damages per violation, and several list
  facial geometry and other biometric identifiers explicitly (EEG is greyer, facial data almost
  certainly qualifies where these laws exist). They typically require a written, publicly available
  retention/destruction policy and written consent *before* collection — real retention/deletion
  code exists (`retention_window`, `expire_signal_rows`) but no written policy document for it.
- **FERPA / school agreements.** The app sits inside a teacher/class/school-year structure, so a
  school deployment likely needs FERPA's "school official" exception, which usually requires a
  written contract between the vendor and the school defining what the vendor may do with student
  records. Nothing in the repo suggests one exists yet.
- **No Terms of Service anywhere** — confirmed by grep, same absence pattern as the privacy policy.
- **No subprocessor/vendor disclosure.** Several state student-privacy laws require an ed-tech
  vendor to disclose who else touches the data. Supabase (all deployments) and Anthropic (when
  `LLM_PROVIDER=claude`) are both real subprocessors with no disclosure found anywhere.
- **No age-verification or minimum-age gate anywhere in signup** — worth confirming this is
  intentional, since COPPA's obligations turn on actual or constructive knowledge a user is under
  13.
- **One real risk confirmed *absent*, worth keeping that way**: no analytics/tracking scripts found
  anywhere in the frontend (checked for Google Analytics, Mixpanel, Segment, PostHog, Amplitude —
  zero matches).

Pick this up as its own piece of work with real legal counsel, not folded into this plan's
implementation order above.

---

# Standing notes for whoever implements this

- **This plan's file has two addenda already folded in** (2026-08-29, 2026-09-02) documenting what
  earlier review found changed. Read the whole plan file, not just this handoff — the addenda carry
  reasoning (like why `_role()` is the right fix and not `app_metadata`) this document doesn't repeat.
- **`LLM_topic_decider.py` line numbers are not stable** — moved 365→607→798 across three PRs adding
  new topics. Grep for `Student Grade Level` rather than trusting any line number here or in the plan.
- **CI is six advisory jobs, none of them a required check** — branch protection needs a paid GitHub
  plan on this private repo, so a red PR still merges. A red `database-migrations` or the new
  `dependency-scan` job is a signal to read, not a gate that stops anything.
- **A new table follows the REVOKE-before-GRANT pattern or it's wide open by default** — see
  CLAUDE.md's *Database — Postgres functions are world-executable by default* section in full before
  writing `security_events` or `login_attempts`. `scripts/check_table_grants.py` /
  `check_function_grants.py` enforce it in CI but only catch a forgotten revoke, not a wrong grant.
- **A migration reaching production is a few minutes after merge, via the Supabase GitHub
  integration** — not synchronous with the merge, and not visible as a check on the PR (the
  "Supabase Preview" check is always `skipped`). Confirm with `npx supabase migration list --linked`
  before assuming a migration has or hasn't landed.
- **Canary discipline applies from the first edit.** This document intentionally carries no suite
  counts because none have been measured this session — don't invent one to fill the table above;
  run the three suites for real once code changes and record what actually came back.
