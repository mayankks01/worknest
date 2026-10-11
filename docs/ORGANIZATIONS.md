# Organizations and tenant isolation

Step 4 introduces real organizations after email verification. The navy sidebar, organization cards, creation form, overview, and settings follow the supplied mockup. `/preview` remains a separate sample-only screen. Authenticated accounts start with their own organization list; no sample tenants are assigned automatically.

## API

All routes use the existing same-origin session and JSON error envelope. POST/PATCH require CSRF. Input fields are limited to `display_name` (1–80 characters), `slug` (3–63 lowercase letters/numbers separated by single hyphens), and an IANA `timezone`. Slugs are globally unique; conflicts return a field error.

| Route | Behavior |
| --- | --- |
| `GET /api/v1/orgs/` | Discover the caller's active memberships and active organizations. Signed actor-bound cursor, default 25 results, maximum 100; accepts `cursor` and `limit`. |
| `POST /api/v1/orgs/` | Verified user creates an organization with one active Owner. Requires an `Idempotency-Key` of 16–128 letters, numbers, underscores or hyphens. Returns 201. |
| `GET /api/v1/orgs/{UUID}/` | Active membership required; unknown/inaccessible/inactive organization returns 404. |
| `PATCH /api/v1/orgs/{UUID}/` | Owner/Admin can update general settings. Member/Viewer gets 403; unrelated caller gets 404. Changed settings create audit/outbox events; no-op updates do not. |

Creation is one transaction containing the organization, Owner membership, audit event, outbox event, and stored response. A user-row lock serializes concurrent creation retries. Keys are scoped to the actor and this creation endpoint, retained for a 24-hour retry window. The canonical validated payload is hashed. Matching requests replay the original response; changed payloads return 409. An expired key can be reused. Replays recheck current visibility before returning saved data.

## Database boundary

`organizations.context.tenant_transaction` installs trusted actor identity and the URL organization UUID through parameterized `set_config(..., true)` (PostgreSQL `SET LOCAL`) inside a transaction. Nested calls restore prior context. Commit/rollback clears local settings; no tenant context is attached permanently to pooled connections. Future workers must use this same explicit boundary.

All five new tables have enabled **and forced** RLS. The normal runtime role is neither their owner nor a superuser, has `NOBYPASSRLS`, and cannot alter policies or truncate tables. No security-definer bypass functions or unrestricted membership lookup role were introduced.

- Tenant reads require matching organization context plus the actor's active membership. HTTP authorization rechecks membership on every request. SQL policies independently recheck it for organization, audit, and outbox access.
- The explicit discovery mode can read only the actor's own active membership rows and corresponding organization metadata. It cannot read audit/outbox tenant content. Membership SELECT policies do not recursively query themselves.
- Creation request records are global actor-scoped entry-point records: there is no organization yet when a request begins. Their RLS requires actor context; the only supported endpoint is organization creation.
- Bootstrap INSERT policies allow a verified actor to create an organization and its own Owner membership in the current transaction. No runtime membership UPDATE/DELETE policy is present in Step 4. Team administration will add narrowly scoped policies and service operations in Step 5.
- A unique partial index allows at most one active Owner. A **deferred composite foreign key** from organization to `(org_id, membership_id, role, state)` requires at least one matching active Owner at commit. Therefore partially created organizations and Owner removal without replacement cannot commit, including via raw SQL.
- Memberships have unique `(org_id, user_id)` and `(org_id, id)` constraints. Audit actors and outbox audit references use composite foreign keys to prevent cross-organization relationships. PostgreSQL enforces these integrity checks regardless of row visibility.
- Audit events are append-only to the runtime role: no UPDATE/DELETE policies. Outbox rows are captured atomically and are intentionally not dispatched yet. Invitation delivery is Step 5; general consumers, retries, audit views and recovery are Step 8.

The context values are a trusted-server boundary, not credentials supplied by clients. RLS protects omitted or incorrect tenant filters; it does not promise safety after arbitrary database credential compromise or arbitrary SQL execution that forges actor context. The HTTP layer never accepts actor/org context fields from JSON.

PostgreSQL references: [row security and integrity checks](https://www.postgresql.org/docs/17/ddl-rowsecurity.html), [transaction-local settings](https://www.postgresql.org/docs/17/sql-set.html).

## Frontend behavior

Switching uses full document navigation to an organization UUID route, dropping component data and unsaved drafts. Restoring a document from browser history reloads it. No tenant data or drafts are persisted in browser storage. Membership and session checks refresh on focus, visibility change, and every minute; API permissions apply immediately on the next request. Lost access clears the displayed workspace. The form keeps the same idempotency key for uncertain retries; an explicit conflict directs the user to their organizations before starting another creation request.

## Verification

The PostgreSQL test suite uses a temporary non-owner, non-superuser, NOBYPASSRLS role for API and adversarial SQL checks. Administrative connections only prepare fixtures and exercise structural constraints. Never point the test runner at production credentials/data.

```powershell
# See ACCOUNTS.md for the local PostgreSQL/Redis test environment.
cd backend
python -m pytest -q
python verify_organizations.py --base-url http://127.0.0.1:5174
# From repository root, inspect the actual runtime role:
docker compose exec -T web python verify_tenant_security.py
```

The HTTP script is restricted to local hosts, creates two disposable accounts and two organizations, and verifies real worker-to-Mailpit delivery before creating tenants. Optional `--ui-account-file` writes disposable login credentials to a caller-selected scratch location outside Git for manual browser QA. Do not commit that file. CI runs both the account and organization HTTP checks and the actual runtime-role check.
