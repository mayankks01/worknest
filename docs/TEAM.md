# Team administration — Step 5

Verified members can view the paginated roster at `/orgs/:id/members`. Owners and Admins see invitations and delivery status. Refresh reloads current roles, quota, and invitation state; each list supports 25-row pages. Mobile tables scroll within their cards.

## Authorization

| Action | Owner | Admin | Member / Viewer |
| --- | --- | --- | --- |
| Read roster | Yes | Yes | Yes |
| Invite / manage Member or Viewer | Yes | Yes | No |
| Invite / manage Admin | Yes | No | No |
| Remove or change Owner through member endpoint | No | No | No |
| Transfer ownership | Current password required | No | No |

Every mutation rechecks live membership after taking the organization usage lock. Removal ends access on the next request and preserves sessions for other organizations. Ownership transfer locks the organization and relevant memberships; the old Owner becomes Admin. A deferred composite foreign key and partial unique index enforce exactly one active Owner at commit.

Step 6 now removes project memberships and clears open-task assignees in the same offboarding transaction, including archived projects. Each affected task receives a new version; completed tasks retain historical attribution. A role change to Viewer clears open assignments while keeping read access. See [projects and tasks](PROJECTS.md).

## Invitations

Invitations expire after 72 hours and require the same normalized, verified account email. Tokens are single-use; resending rotates the version and invalidates the old link. Only one pending invitation per email and organization exists. Accepting while already an active member consumes the invitation without changing the existing role. Removed memberships can rejoin through a new valid invitation.

Links use a URL fragment, which the client removes immediately and keeps in memory. Sign-in on the invitation page preserves that token. New users create and verify an account in another tab, then return; after refreshing the invitation page, reopen the email link. Preview validates the account and token without consuming the invitation. No raw token is stored in the database: a server-keyed HMAC can reconstruct the secret for delivery, while acceptance checks its SHA-256 digest. Rotating the Django secret invalidates outstanding links.

The default active-member quota is 30. `ORGANIZATION_MEMBER_LIMIT` controls newly created organizations; existing quotas live in `OrganizationUsage`. The migration backfills existing active counts. All accepts and team writes serialize on this row, including concurrent claims for the final place. Pending invitations do not reserve places.

Invitation creation requires a 16–128 character `Idempotency-Key`, scoped to actor, organization and this endpoint for 24 hours. Replaying changed data returns 409. Invitation create, resend, revoke, preview, and accept are throttled; ownership password checks have a separate throttle. Mutations require CSRF.

## API

All tenant paths start with `/api/v1/orgs/:org_id/`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `members/` | Active roster, current role and quota |
| PATCH / DELETE | `members/:member_id/` | Change role / remove |
| GET / POST | `invitations/` | Paginate / create |
| POST | `invitations/:id/resend/` | Rotate token and queue delivery |
| POST | `invitations/:id/revoke/` | Invalidate invitation |
| POST | `transfer-ownership/` | Target member ID plus current password |

Global POST endpoints `/api/v1/invitations/preview/` and `/api/v1/invitations/accept/` accept `{token}`. Hidden tenant resources return 404; forbidden actions on visible resources return 403. Pagination cursors bind to actor, organization and list type.

## Database and delivery

The application role remains non-owner, non-superuser and NOBYPASSRLS. All eight organization/team tables force RLS. New invitation relationships have composite same-organization foreign keys. Fixed-search-path SECURITY DEFINER helpers read only the current actor/organization role, active state, owner reference, or matching invitation capability. The table-owner branch is bounded for these reads; the invitation-job discovery helper can read invitation outbox rows and returns only due organization/event IDs. Runtime workers must establish a single organization and event context to read a recipient or update delivery. No helper accepts arbitrary SQL or disables RLS. See PostgreSQL's [SECURITY DEFINER guidance](https://www.postgresql.org/docs/17/sql-createfunction.html).

Invitation creation, audit and outbox capture commit together. A post-commit enqueue is best effort; beat recovers due jobs every 30 seconds. Workers claim a two-minute lease, send SMTP outside the transaction, and retry with backoff/jitter up to five attempts. Closed, expired and superseded jobs become obsolete. Failed jobs can be recovered through Resend. SMTP delivery is at least once: a crash after provider acceptance but before recording success may duplicate an email; its invitation remains single-use. General notification/inbox processing remains Step 8.

In local development, all emails are captured by Mailpit at http://127.0.0.1:8025. A sent status means the SMTP server accepted the message, not that it reached an external inbox. External email delivery requires a later provider configuration.

## Verification

Run `pytest -q`, `ruff check .`, `ruff format --check .`, `manage.py check`, and `manage.py makemigrations --check --dry-run` in the backend test environment. Build the frontend with `npm run build`. With Compose services and the frontend running, run `python backend/verify_team.py --base-url http://127.0.0.1:5174` and `docker compose exec -T web python verify_tenant_security.py`. The HTTP smoke creates disposable local accounts, verifies their email through Mailpit, and exercises invitation acceptance, roles, transfer, removal and rejoin. CI includes this smoke.
