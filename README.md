# WorkNest

A private home for a team's projects and tasks. Built from the WorkNest PRD v1.0, with an approval checkpoint after every stage.

**Current scope: Step 9 search and data lifecycle, plus notifications, comments/files, projects/tasks, teams, organizations and accounts.** Search respects live project access. Owners/Admins can request private exports; Owners can schedule organization deletion with a seven-day cancellation window. Durable jobs enforce retention and cleanup. See [data lifecycle](docs/LIFECYCLE.md) and [Step 9 verification](docs/STEP-9-VERIFICATION.md). This is a local development build, not a public deployment.

## Quick start with Docker

Requirements: Docker Desktop running in Linux container mode and Docker Compose v2. Use an unused set of local ports: 5173, 8000, 55432, 6379, 1025, 8025, 9000, 9001, and 3310. PostgreSQL uses host port 55432 to avoid existing local database services; change `DATABASE_PORT` in `.env` if needed.

From this directory:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

On macOS/Linux use `cp .env.example .env` for the first command. Keep an existing `.env` when returning to the project.

Open the app at http://localhost:5173. The frontend proxies `/api` to Django on the same origin. Create an account, then open http://localhost:8025 to find its verification email in Mailpit. Local emails are captured here instead of being sent externally. Set `PUBLIC_APP_URL` in `.env` to the exact frontend origin, then recreate web/worker/beat if it changes. Account details and tests are documented in `docs/ACCOUNTS.md`.

After verification, `/` opens your real organization list. Create an organization, open its overview, or change settings as its Owner. See `docs/ORGANIZATIONS.md` for API and isolation details. The sample dashboard remains at `/preview` and runs without a backend. For native frontend development, run `npm ci` and `npm run dev` in `frontend/`; use `npm run dev -- --port 5174` when needed and match `PUBLIC_APP_URL` to that port. Records inside the design preview still reset on refresh and are never assigned to real accounts.

| Service | Address / purpose |
| --- | --- |
| Web client | http://localhost:5173 |
| API liveness | http://localhost:8000/api/v1/health/live/ |
| API readiness | http://localhost:8000/api/v1/health/ready/ |
| Email sandbox | http://localhost:8025; SMTP on localhost:1025 |
| Private object storage | S3 endpoint localhost:9000; local console http://localhost:9001 |
| PostgreSQL | localhost:55432 (container port 5432) |
| Redis | localhost:6379 |
| ClamAV scanner | localhost:3310; allow up to 4 GiB RAM and initial signature download time |

All host port mappings bind to loopback. `.env.example` contains local development credentials only. RustFS and its root credentials are local infrastructure placeholders; select and validate the pilot storage provider and a restricted service account before deployment. The initialization job creates a private bucket and refuses unexpected bucket policies. Application files now remain private and quarantined until their checks pass; downloads go through the authenticated API.

View logs with `docker compose logs -f web worker`. After changing source, run `docker compose up -d --build` again. Stop with `docker compose down`; database and object volumes remain intact. Do not delete volumes to fix routine startup issues.

## Native development on Windows

Use Python 3.12 and Node 22.16 or newer compatible Node LTS. Docker remains the intended source of PostgreSQL, Redis, email, and object storage.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
docker compose up -d db redis mailpit storage-init scanner worker beat
docker compose run --rm migrate
.\scripts\run-backend.ps1 -Python .\.venv\Scripts\python.exe
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

The PowerShell helper loads `.env` without evaluating it as code. Containers receive the same configuration through Compose. For other shells, export the environment variables from `.env` before running `python backend/manage.py runserver`.

Account tests now require PostgreSQL and Redis. Dependency-free health tests can still run separately. The readiness endpoint returns 503 when its required services are unavailable.

## Checks

From the project root in PowerShell:

```powershell
$env:DJANGO_SECRET_KEY='local-check-only'
$env:DJANGO_DEBUG='true'
cd backend
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
# Run the database-backed account tests using the test-only environment in docs/ACCOUNTS.md.
cd ../frontend
npm run build
```

The suite covers health checks plus registration, verification, recovery, CSRF, session rotation/expiry/revocation, token races, atomic Redis limits, and email retries. CI uses disposable PostgreSQL/Redis services. A separate local HTTP smoke check exercises real Celery delivery to Mailpit; see `docs/ACCOUNTS.md`.

CI is defined in `.github/workflows/ci.yml`. Organization checks cover forced RLS under a restricted role, Owner/composite constraints, concurrent idempotency, role permissions, immediate membership revocation, and transaction-context cleanup. See `docs/STEP-4-VERIFICATION.md` for local results; saving a workflow does not prove a hosted run passed.

## Structure and boundaries

```text
backend/config/       Django configuration, routes, WSGI, Celery
backend/foundation/   Health endpoints and request IDs
backend/accounts/     Global identities, account tokens, email jobs and API
backend/organizations/ Organizations, memberships, tenant context, RLS, audit/outbox capture
backend/projects/     Private projects, memberships, versioned tasks and project policy
backend/collab/       Comments, private attachments, scanning and storage cleanup
backend/tests/        Foundation, account, team, project, collaboration and tenant checks
frontend/src/         Account, organization, team and project UI, plus isolated preview fixtures
infra/postgres/       Initial database roles and grants
scripts/              Local startup helper
docs/                 Architecture and stage verification
.github/workflows/    Backend, frontend, and integration checks
```

Migrations run as `worknest_owner`; web and worker connections use `worknest_app`, which has no superuser, role creation, database creation, table ownership, or BYPASSRLS privilege. Initial grants apply to tables created by the migration role. All fourteen organization/team/project/collaboration tenant tables have forced RLS. New tenant tables must receive transaction scoping, policies, relationship constraints, and isolation tests when introduced.

Step 3 defines the custom user before installing Django authentication, avoiding a premature default-user migration. The original content-type-only foundation has now been migrated to the custom user and session schema.

The dependency versions are exact in Python requirements and the npm lockfile. PostgreSQL, Redis and ClamAV use verified immutable image digests; the other container images use explicit release tags. Use scanned immutable image digests for every image before pilot deployment. Google Fonts enhance the preview typography, with system font fallbacks if offline.

## Next checkpoint

After reviewing Step 7, authorize **Step 8: notifications and reliability** (inbox, email preferences, audit views, failed jobs and reconciliation). Do not start another stage without the user's approval. Completed steps are committed and pushed to GitHub before asking to proceed.
