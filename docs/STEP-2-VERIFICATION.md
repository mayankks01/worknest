# Step 2 verification

Date: 10 October 2026. Scope: project foundation only.

The original welcome screen below has since been replaced by the mockup-based UI preview. See [UI verification](UI-VERIFICATION.md) for its checks and screenshots; the foundation results remain historical evidence.

## Completed checks

| Check | Observed result |
| --- | --- |
| Frontend TypeScript and production build | Passed with the pinned npm dependencies. |
| Browser rendering | Welcome screen inspected in the normal app preview and at 390px width. |
| Browser interaction | Connection check works by pointer and Enter key; status updates through a live API request. No browser console errors were reported in the inspected session. |
| Django configuration | `manage.py check` reported no issues. |
| Backend lint and formatting | Ruff checks passed. |
| Backend foundation tests | 5 passed: liveness independence, readiness success/failure, safe error responses, request IDs, and write-method rejection. These tests mock dependencies. |
| Python dependency consistency | `pip check` reported no broken requirements. |
| Compose configuration | `docker compose config --quiet` passed. |
| Live PostgreSQL and Redis | Both containers became healthy; readiness through the React development proxy returned HTTP 200 and both dependency checks were `ok`. |
| Initial migrations | Both Django content-type migrations applied with the owner role; `makemigrations --check --dry-run` reported no changes. |
| Database roles | Application and migration roles have no superuser, BYPASSRLS, CREATEDB, or CREATEROLE privileges. Both existing tables belong to the migration role. |
| Real dependency outage behavior | Before infrastructure startup, readiness returned HTTP 503 with safe dependency status and a request ID; liveness remained HTTP 200. |
| Email sandbox | Local SMTP `NOOP` returned 250. No email was sent. |
| Source hygiene | `.env`, frontend dependencies, and generated frontend build output are ignored by Git. |

## Container integration

Both application images built successfully. The full container stack passed its startup checks. The frontend returned HTTP 200, and readiness through its same-origin proxy returned HTTP 200 with PostgreSQL and Redis both healthy.

`verify_infrastructure.py` passed all five live integration checks: restricted application database role, PostgreSQL/Redis readiness, Celery broker round trip, SMTP connection, and signed object reads with anonymous requests denied by HTTP 403. The temporary probe object was removed afterward.

The storage bootstrap completed successfully twice, including a rerun against the existing bucket. The container migration check reported no changes. CI's startup sequence separates the one-time bucket bootstrap from continuously running services so `--wait` does not misinterpret a successful initialization exit.

Local verification is complete. The project services are stopped after verification to release resources; data volumes are retained. Start them again with `docker compose up -d` from the project root. Temporary native preview servers were also stopped. The reviewed image is saved as `foundation-preview.jpg` beside this report.

## Scope and limits

- This is a local development foundation. Accounts, tenants, RLS policies, role enforcement, and business workflows are not implemented yet.
- The runtime database account is prepared for RLS, but no tenant-isolation claim can be made before Step 4's schema, policies, and adversarial tests.
- GitHub Actions is configured but has not run on GitHub. No repository has been pushed or published.
- No load testing, restore drill, production hardening, or pilot deployment has been performed. These belong to later approved stages.
- Windows blocked local port 5432; the documented PostgreSQL host port is 55432. Containers still use port 5432 internally.

## Reproduce live integration checks

After the full Compose stack is healthy and `storage-init` exits successfully:

```text
docker compose exec -T web python verify_infrastructure.py
```

This checks the application role, PostgreSQL/Redis readiness, a Celery ping, a local SMTP connection, and signed versus anonymous S3 object access. It creates one uniquely named probe object and removes that exact object afterward. It does not create user accounts or send email.
