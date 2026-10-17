# Recovery and release runbook

## Repeatable local drill

Start local services using the README. From the repository root, with Python installed, run:

```text
python backend/release_review.py --output ../review-small
python backend/release_review.py --output ../review-load --tenants 100 --tasks 1000 --benchmark
```

Use a new output directory outside Git each time. The script creates new worknest_review_* databases, applies all migrations from scratch, seeds unrelated organizations, dumps with pg_dump custom format, restores to a second new database, compares task counts/content hashes, checks validated constraints and verifies all 19 RLS tables as the real application role. It advances a synthetic restored organization's deadline and exercises real object round-trip restoration and scoped purge with a durable receipt. A neighboring object's bytes must survive unchanged. No user database is overwritten or dropped. Review databases and the synthetic dump are retained for inspection. The source fixture contains disposable session cookies; never upload it or include it in CI artifacts.

The script assumes the local Compose superuser is postgres and reads configured owner/application role identifiers from .env. Application and migration commands still run under their separate configured roles. It binds the optional review API only to loopback port 18000. Do not run against production. Inspect the exact generated database names before separately removing review resources. The script deliberately has no automatic database deletion step.

## Production backup policy (deployment gate)

Before pilot release, configure daily encrypted PostgreSQL backups and coordinated private-object backup/version history in the selected region. Restrict backup access and keep encryption keys separately. Expire all database/object backup copies within 30 days, including replicas and exported copies. Alert on failed backup or backups older than 24 hours. Test recovery using the actual provider. RPO <=24 hours and RTO <=4 hours are targets, not proven by a local pg_restore duration.

A pg_dump alone does not back up attachment bytes, roles or provider configuration. Capture the schema/role migration revision, private-object inventory/checksums, backup timestamp and external deletion ledger. The local fixture drill is unencrypted synthetic data; it does not establish encrypted production backup compliance.

## Restore procedure

1. Create an isolated environment with outbound email disabled and workers/beat stopped. Never restore over the only live copy. Restore roles/grants with the deployment secret manager, not copied plaintext credentials.
2. Restore the database and corresponding private objects. Validate constraints, counts and checksums; run migrations only after checking the backup's application revision. Run verify_tenant_security.py using the non-owner runtime role.
3. Invalidate restored sessions and outstanding verification/reset/invitation links before opening traffic. Reconcile private file reservations and quarantined objects.
4. Apply deletion requests from an independently retained deletion ledger after the backup timestamp. Re-run overdue purges and confirm receipts/object removal before enabling access. A restored older database can otherwise resurrect deleted organizations. This external ledger/reconciliation is a required deployment control, not implemented by the local Compose stack.
5. Verify representative accounts, isolated projects and private files; complete smoke checks. Re-enable workers with email suppression until old queued work is reconciled. Record actual recovery time and data loss. Obtain deployment approval before switching traffic.

## Safe release and rollback

Capture a fresh recoverable backup before destructive migrations. Rehearse migrations on a copy; validate runtime RLS and constraints after applying them. Prefer additive schema changes. Roll back an application image only if it remains compatible with the migrated schema; otherwise forward-fix or restore the isolated verified copy using the procedure above. Never disable RLS to make a release pass.

Monitor API 5xx/latency, DB saturation, oldest durable queue age, exhausted retries, storage capacity and backup age. Alert at >2% 5xx for five minutes, queued work older than ten minutes, or a failed backup. Configure these in the selected hosting/monitoring service before pilot. Keep passwords, tokens, cookies, task text and file contents out of logs. Request IDs correlate HTTP failures; durable event/job IDs support worker investigation.
