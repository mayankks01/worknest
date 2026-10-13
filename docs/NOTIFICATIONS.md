# Notifications and reliability

Step 8 adds organization-scoped notifications, assignment email preferences, audit history and delivery recovery. Open Notifications from the workspace sidebar. Owners and Admins also see Audit history and Delivery & recovery.

## Notification behavior

- Assigning a task to another eligible member creates an inbox notification. Replaying a write or processing the same event twice creates no duplicate inbox record.
- New comments notify the task creator, assignee and previous comment authors who have access, excluding the comment author. Comments do not send email.
- Recipients must be eligible when the write happens and when the worker processes it. Inbox requests and task links check current project access. Removed members and deleted tasks are hidden immediately.
- Assignment email is enabled by default and configurable per member, per organization. Disabling it does not disable inbox, invitation or account messages. Pending email checks the current assignee, membership, project access and preference before sending.
- Email contains a generic message and an authenticated task link; it omits task titles, descriptions, comments and member names. Links open the task dialog after authorization.
- Read/unread controls and the unread-only filter apply to the current member. Refresh checks for new updates; this release does not use real-time subscriptions.

## Delivery and recovery

Business writes commit their audit and outbox records atomically. Broker publication happens after commit; failed publication leaves durable work for the 15-second dispatcher. Workers claim jobs with a five-minute lease. Failures use exponential delay plus jitter and stop after five attempts. Error records use generic codes rather than provider responses or user content.

Inbox uniqueness is enforced in PostgreSQL. Email uses a stable application-generated Message-ID, which is not a provider delivery receipt. SMTP acceptance followed by a connection or process failure can still cause a duplicate email on retry. A revocation racing an already accepted message cannot recall it; the link always rechecks authorization.

Delivery & recovery shows pending event count, the oldest pending event, the last successful storage/quota check, and failed events/invitations, assignment emails, file cleanup and reconciliation. Owners/Admins can queue a retry after repairing the supporting service. Retry requests are audited. Failed content/scanning uploads are handled from the task by uploading again; this screen retries exhausted storage cleanup only.

Old outbox rows without notification recipient payloads are processed without generating historical alerts. Existing organizations receive a reconciliation schedule during migration.

## Reconciliation and isolation

Every five minutes, a durable organization job compares usage counters with active memberships, unarchived projects, non-deleted tasks and attachment reservations. It serializes corrections with normal quota writes and records corrected counter names in the audit. A successful check scans one storage page (up to 1,000 keys), retaining a continuation cursor for the next run.

Object cleanup considers only canonical attachment UUID keys under the exact organization prefix, older than 24 hours. It deletes missing-row objects or terminal file objects with no reservation or pending cleanup. Active/quarantined files and reserved space remain protected. S3 and SMTP operations run outside business database transactions. Counter corrections may commit even if the later storage check fails; the success timestamp updates only after the storage page completes.

Step 8 introduced 17 tenant tables with forced RLS (19 after Step 9). The new tables have organization-local relationship constraints. Background contexts are anchored to an existing job and exact organization; routing helpers expose IDs only. Audit views are Owner/Admin-only and have no edit/delete endpoint; ordinary actor contexts cannot update or delete audit rows. Step 9 grants only exact maintenance-job contexts deletion access for retention and organization purge.

## API

All paths below are relative to `/api/v1/orgs/{org_id}/` and use authenticated sessions. Mutations require CSRF.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `notifications/?unread=true` | Own accessible inbox; unread count and cursor pagination |
| PATCH | `notifications/{id}/` | Set `read` to a boolean |
| GET/PATCH | `notifications/preferences/` | Read/set boolean `assignment_email` |
| GET | `audit/` | Owner/Admin audit history |
| GET | `operations/?kind=event` | Failed jobs; kinds: event, email, file, reconcile |
| POST | `operations/{kind}/{id}/retry/` | Owner/Admin retry; empty JSON object |

Audit and inbox cursors are signed and scoped to the actor and organization. Account delivery remains covered by the existing account-token recovery worker.

## Local verification and remaining scope

Run `python backend/verify_activity.py --base-url http://localhost:5173 --pause-worker` with Compose services available. It creates disposable local accounts, pauses/restarts the worker, verifies committed work and recovery, and checks Mailpit delivery. Local mail stays in Mailpit; external provider delivery is not claimed. CI runs this workflow after collaboration verification.

Search, exports, deletion workflows, 90-day audit retention and 30-day notification retention are implemented in Step 9; see `LIFECYCLE.md`. Production hosting, external email/storage configuration and release readiness remain future stages.
