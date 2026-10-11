# Accounts and sessions — Step 3

Implemented from PRD FR-01. Users are global identities, not tenant-owned rows. Organization creation/join remains Step 4/5; those routes must apply `HasVerifiedEmail` in addition to their membership and role checks. An unverified account may sign in and request another verification email.

## API

All routes are under `/api/v1/auth/`, use JSON, and end in `/`. Unknown input fields are rejected. Fetch `session/` first and send its `csrf_token` as `X-CSRFToken` on **every POST**, including anonymous login/registration/reset. Keep cookies on the same origin. CSRF rotates at login; the frontend refreshes it before each write.

| Method and path | Input | Success |
| --- | --- | --- |
| GET `session/` | None | 200: `csrf_token`, `user` (or null) |
| GET `me/` | Authenticated cookie | 200: `user`; otherwise 401 |
| POST `register/` | `display_name`, `email`, `password` | Generic 202; queues verification for a new account |
| POST `login/` | `email`, `password` | 200: `user`, new `csrf_token`, session cookie |
| POST `logout/` | `{}` | 200; flushes current session |
| POST `resend-verification/` | `email` | Generic 202 |
| POST `verify-email/` | `token` | 200; consumes verification link |
| POST `password-reset/` | `email` | Generic 202 |
| POST `password-reset/confirm/` | `token`, `password` | 200; invalidates prior passwords/sessions/links |

Public user shape: UUID `id`, normalized `email`, `display_name`, nullable UTC `verified_at`. No password hashes, staff flags, token secrets, or delivery internals are returned. Errors use `{error:{code,message,request_id,details}}`. Field errors are 400, unauthenticated requests 401, CSRF failures 403, throttling 429 with `Retry-After`, and database/Redis failures inside account endpoints 503.

## Security choices

- Email is trimmed/lowercased; uniqueness and normalization have database constraints. This is the MVP email normalization policy, including the local part.
- Passwords use Django's default PBKDF2 hasher. Validation requires 12–128 characters and rejects common, numeric, or personal-detail-like passwords. Fast MD5 hashing exists only in isolated test settings.
- Server-side database sessions have an absolute 12-hour lifetime. Every successful login rotates the session key and CSRF secret. Logout flushes that session. Password reset causes all old session hashes to fail on their next request. Inactive accounts cannot authenticate or use old sessions.
- Session cookies are HttpOnly and SameSite=Lax. Secure cookies and HTTPS redirect are enabled when `DJANGO_DEBUG=false`. Local HTTP deliberately uses debug mode. A production same-origin HTTPS proxy still requires deployment configuration and validation.
- Verification links expire in 24 hours (implementation default); reset links expire in exactly 30 minutes (PRD requirement). They are single-use, purpose-bound, and invalid after a password change. Resending replaces earlier links of the same purpose. User-row and token-row locks serialize consumption and issuance races.
- Token secrets are HMAC-derived using the server secret, random token UUID, purpose, and password hash. Only SHA-256 token digests are stored; worker messages contain token IDs. Links use URL fragments, not query parameters, and the browser clears the fragment after loading. Reloading that page requires reopening the original email link. Pages send a no-referrer policy.
- Registration, recovery, and resend use generic accepted responses to avoid direct account enumeration. Login failures are generic. This does not claim perfect network-level timing equivalence.
- Atomic Redis counters limit both IP and normalized-email attempts; keys contain keyed digests, not raw addresses. Redis failure fails closed. Defaults: login 30/IP and 10/email per 15 minutes; register 10/IP and 5/email per hour; reset/resend 10/IP and 3/email per hour; token consumption 30/IP per 15 minutes. Fixed-window limits are not a substitute for edge DDoS protection.
- IP limits use `REMOTE_ADDR`, never arbitrary forwarded headers. Vite is a local development proxy and can aggregate clients onto its address. A deployed trusted proxy must establish accurate client addresses before pilot release.

## Email delivery

`AccountToken` also holds a durable, global account-email job. Creation and job insertion are atomic. The post-commit publish is best-effort; one Celery beat scheduler scans pending jobs every 30 seconds. Workers claim a two-minute lease in a short transaction, then call SMTP **outside** that transaction. Failures use exponential delay with jitter, up to five attempts; terminal failures remain recorded. Expired/consumed tokens cannot deliver usable links. A resend creates a fresh job.

Duplicate tasks do not resend an already completed job. An ambiguous SMTP timeout or a worker crash after acceptance can still produce duplicate email delivery; this is not exactly-once SMTP. A dead worker's fifth lease is marked failed when it expires. Later tenant outbox work remains a separate stage.

Run one `beat` plus `worker` for automatic recovery. The local email inbox is [Mailpit](http://localhost:8025); no external email service is configured. `PUBLIC_APP_URL` must match the browser origin and port so links return to the correct app. Host headers never determine outgoing link destinations.

## Frontend routes

`/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify-email`, and `/resend-verification` share the approved navy/blue visual design. `/` shows the actual account state after login. `/preview` retains the explicit sample dashboard; it is not authenticated tenant data. Session state refreshes on focus/visibility and once a minute. Authentication is never stored in localStorage.

## Verification commands

Backend tests require PostgreSQL and Redis. Run tests with a **test-only database role able to create an isolated test database**, not the production application role. CI supplies disposable PostgreSQL/Redis services and uses `config.test_settings`. Local PowerShell example (in a separate terminal):

```powershell
$env:DJANGO_SECRET_KEY='local-test-only'
$env:DJANGO_DEBUG='true'
$env:DATABASE_HOST='127.0.0.1'
$env:DATABASE_PORT='55432'
$env:DATABASE_USER='postgres'
$env:DATABASE_PASSWORD='local-postgres-password'
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

Use your actual local `.env` test database credentials if changed. These example credentials apply only to this loopback development stack. Do not use the test shell to start the application with elevated database credentials.

With the app, worker, beat, and Mailpit running, run `python backend/verify_accounts.py --base-url http://localhost:5173`. This sends real local SMTP mail through Celery and verifies CSRF, registration, verification, reset, cross-session revocation, and logout over HTTP. It creates one disposable `worknest-smoke-…@example.test` identity; it never prints its passwords or tokens. It refuses non-local destinations. Default URL is the current native preview on port 5174.

Reference design follows Django's [custom-user guidance](https://docs.djangoproject.com/en/5.2/topics/auth/customizing/), [CSRF protection](https://docs.djangoproject.com/en/5.2/ref/csrf/), and [session management](https://docs.djangoproject.com/en/5.2/topics/http/sessions/).
