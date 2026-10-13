# Search and data lifecycle

Step 9 adds organization-scoped search, private exports and retention/deletion workers. All routes require an authenticated session; mutations require CSRF. Nineteen tenant tables force PostgreSQL RLS under the non-owner application role.

## Search

`GET /api/v1/orgs/{org_id}/search/?q=words` matches task titles and descriptions (2–200 trimmed characters). Results and counts include only currently accessible projects and exclude deleted tasks. `state=active` is the default; `archived` and `all` are supported, as is an optional project UUID. Signed pagination cursors bind the organization, actor and filters. Search is available in the workspace navigation.

## Private exports

Owner/Admin users request exports with `POST exports/`, an empty JSON object and an Idempotency-Key header. Repeated requests with the same key return the same job. At most two queued/running exports are allowed per organization. `GET exports/` lists the requester's own jobs; `GET exports/{id}/` reads one; `POST exports/{id}/` retries a failed job. These paths are relative to the organization API root.

Workers produce a private ZIP with `workspace.json`, project/task metadata, non-deleted comments, member display names and available scanned attachments. Archived projects are included; deleted tasks, quarantined files, account credentials, invitation tokens and personal notification preferences are excluded. Download uses `GET exports/{id}/download/`; only the requester with a current Owner/Admin role can download. Permissions are checked before generation, before publication and before/after storage reads. Downloads validate the stored size and SHA-256 and expire 24 hours after publication. Storage I/O runs outside business transactions; temporary ZIP spooling limits memory use. Exports have separate temporary storage, not attachment quota reservations.

## Organization deletion

`GET deletion/` is Owner/Admin-only. Only the Owner may `POST deletion/` with action `schedule` or `cancel`, the current password and the organization slug. The Data & privacy form supplies these fields. Scheduling sets a seven-day deadline; repeat scheduling does not extend it. Normal authorized work, including invitations, continues during this cancellation window. Cancellation restores active status. At the deadline normal access ends even if the worker is unavailable.

Maintenance waits an additional ten minutes before physical purge to allow bounded in-flight work to finish. It deletes objects only under the exact organization prefix, confirms the prefix is empty, then removes tenant rows and related creation retry records in a transaction. Storage errors preserve database records for retry. The operational target is completion within 24 hours after the deadline; operators must recover exhausted jobs/outages to meet it. A maintenance receipt retains only identifiers, timestamps and generic job state after the organization is gone. Global user accounts are not deleted by organization deletion.

Backups may retain copies for up to 30 days. This is the required deployment policy disclosed in the UI, not a claim that backup infrastructure has been deployed. Backup expiry, restore procedures and restore-time deletion enforcement remain the Step 10 release gate.

## Retention and recovery

Maintenance runs every five minutes, with a durable dispatcher every 30 seconds. Soft-deleted tasks are purged after 30 days together with comments/files; storage quota is released only after confirmed object deletion. Notifications expire after 30 days; audit history expires after 90 days. Audit retention also retires associated outbox jobs and their references, including abandoned delivery work. Expired export objects and unreferenced export objects older than 24 hours are cleaned up. Expired idempotency records are removed. Export job metadata remains until organization purge.

Jobs have five-minute leases, bounded worker execution and five attempts with exponential backoff plus jitter. Exact organization/job capabilities scope background RLS. Failed exports can be retried in Data & privacy. Maintenance failures are shown there while the organization remains accessible; after its deadline, an operator must recover the job.

An operator with authorized database access can identify the organization and maintenance UUID from `lifecycle_maintenance`, then run the scoped command through the application's restricted database role:

```text
python manage.py lifecycle_status ORG_UUID JOB_UUID
python manage.py lifecycle_status ORG_UUID JOB_UUID --retry
```

The first command is a read-only dry run/receipt containing deadline, task purge count and generic status. The second only requeues an exhausted failed job; the worker still checks all deadlines. Do not bypass RLS or manually remove storage/tenant rows to recover a failed job. Monitor failed jobs and the age of pending deadlines in production. No production deployment or backup SLA is claimed by this local build.

## Verification

See [Step 9 verification](STEP-9-VERIFICATION.md). The local integration check is `python backend/verify_lifecycle.py --base-url http://localhost:5173 --account-file PATH`, using disposable credentials produced by `verify_activity.py --ui-account-file PATH`. Keep that credential file outside Git. CI runs the same checks against disposable services.
