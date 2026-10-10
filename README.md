# WorkNest

A private home for a team's projects and tasks. Built from the WorkNest PRD v1.0, with an approval checkpoint after every stage.

**Current scope: Step 2 foundation plus the requested UI template update.** A frontend-only dashboard, organization directory, project table, task list, and board follow the user's supplied mockup. They use explicitly labeled sample data and temporary browser state. The Django health API, local services, and initial CI remain in place. Authentication, persisted organizations/tasks, RLS policies, and business workflows remain reserved for later approved stages. This is not ready for a public deployment.

## Quick start with Docker

Requirements: Docker Desktop running in Linux container mode and Docker Compose v2. Use an unused set of local ports: 5173, 8000, 55432, 6379, 1025, 8025, 9000, and 9001. PostgreSQL uses host port 55432 to avoid existing local database services; change `DATABASE_PORT` in `.env` if needed.

From this directory:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

On macOS/Linux use `cp .env.example .env` for the first command. Keep an existing `.env` when returning to the project.

Open the app at http://localhost:5173. The frontend proxies `/api` to Django so the browser uses one origin. The current UI preview runs without a backend connection and makes no business API calls; its sample profile is not a signed-in session. Health endpoints remain available separately. See `docs/UI-DESIGN.md` for the visual baseline and preview boundaries.

For a frontend-only preview, run `npm ci` and `npm run dev` in `frontend/`. No database or Docker startup is needed for this design preview. Search, switch organizations, open a project, and use Board/List to review the template. Refresh or use Reset preview to restore the sample data.

| Service | Address / purpose |
| --- | --- |
| Web client | http://localhost:5173 |
| API liveness | http://localhost:8000/api/v1/health/live/ |
| API readiness | http://localhost:8000/api/v1/health/ready/ |
| Email sandbox | http://localhost:8025; SMTP on localhost:1025 |
| Private object storage | S3 endpoint localhost:9000; local console http://localhost:9001 |
| PostgreSQL | localhost:55432 (container port 5432) |
| Redis | localhost:6379 |

All host port mappings bind to loopback. `.env.example` contains local development credentials only. RustFS and its root credentials are local infrastructure placeholders; select and validate the pilot storage provider and a restricted service account before deployment. The initialization job creates a private bucket and refuses unexpected bucket policies. No application uploads or downloads are implemented yet.

View logs with `docker compose logs -f web worker`. After changing source, run `docker compose up -d --build` again. Stop with `docker compose down`; database and object volumes remain intact. Do not delete volumes to fix routine startup issues.

## Native development on Windows

Use Python 3.12 and Node 22.16 or newer compatible Node LTS. Docker remains the intended source of PostgreSQL, Redis, email, and object storage.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
docker compose up -d db redis mailpit storage-init
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

If infrastructure is unavailable, `manage.py check` and the isolated tests below still run. The readiness endpoint correctly returns 503; those tests are not proof of a working database, worker, or object store. Django's normal development server requires database access for its startup migration check.

## Checks

From the project root in PowerShell:

```powershell
$env:DJANGO_SECRET_KEY='local-check-only'
$env:DJANGO_DEBUG='true'
cd backend
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m pytest -q
cd ../frontend
npm run build
```

The health tests verify dependency-free liveness, readiness success/failure, redaction of dependency errors, request IDs, and HTTP method restrictions. They deliberately mock dependencies. Live integration checks belong to the separate CI job, which starts PostgreSQL/Redis and validates API readiness, migrations, frontend proxying, and the application database role.

CI is defined in `.github/workflows/ci.yml`. It runs only after this repository is connected to GitHub; a saved workflow is not evidence of a successful hosted run. See `docs/STEP-2-VERIFICATION.md` for the actual local results and limitations.

## Structure and boundaries

```text
backend/config/       Django configuration, routes, WSGI, Celery
backend/foundation/   Health endpoints and request IDs
backend/tests/        Foundation checks
frontend/src/         React/TypeScript UI preview, components, and sample fixtures
infra/postgres/       Initial database roles and grants
scripts/              Local startup helper
docs/                 Architecture and stage verification
.github/workflows/    Backend, frontend, and integration checks
```

Migrations run as `worknest_owner`; web and worker connections use `worknest_app`, which has no superuser, role creation, database creation, table ownership, or BYPASSRLS privilege. Initial grants apply to tables created by the migration role. No tenant tables exist yet, so there are no RLS policies to test at this stage. Tenant transactions and forced RLS arrive in Step 4 and must be implemented on each tenant table as it is introduced.

Auth applications are deliberately not installed before defining the custom user model in Step 3, avoiding a premature default-user database migration. The only initial Django migration is for content types.

The dependency versions are exact in Python requirements and the npm lockfile. PostgreSQL and Redis use verified immutable image digests; the other container images use explicit release tags. Use scanned immutable image digests for every image before pilot deployment. Google Fonts enhance the preview typography, with system font fallbacks if offline.

## Next checkpoint

After reviewing Step 2, authorize **Step 3: accounts and authentication** to implement registration, verified email, login/logout, password reset, secure sessions, and their screens. Do not start another stage without the user's approval.
