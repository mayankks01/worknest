# Step 8 verification

Verified locally on 13 October 2026. Scope: notification inbox, assignment email preferences, audit history, failed-job recovery and quota/storage reconciliation. No production deployment or external email provider is claimed.

## Automated checks

- Full PostgreSQL regression run: 170 passed, one recovery-test timing failure. Its due time was set to the host's current instant while routing compares the Docker database clock. The test now sets the job one minute overdue. The complete activity module then passed all 16 tests, covering the corrected case and every other new notification test. All 171 collected cases have passing results across these runs.
- New tests cover duplicate/concurrent outbox consumers, recipient selection, revoked membership/project access, task deletion/reassignment, own-member inbox and signed-cursor isolation, preferences, SMTP outside transactions, five-attempt failure and retry, broker outage, atomic consumption rollback, append-only audit behavior, cross-organization worker context, composite foreign keys and reconciliation/orphan boundaries.
- Ruff check and format check passed (83 Python files).
- TypeScript check and Vite production build passed.
- Django system check passed; migration drift check reported no changes.
- Actual runtime-role verification passed: non-owner, non-superuser, NOBYPASSRLS, with all 17 tenant tables forcing RLS, denying TRUNCATE and failing closed without context.

## Live services

`verify_activity.py --base-url http://127.0.0.1:5174 --pause-worker` passed against PostgreSQL, Redis, Celery, private S3 storage and Mailpit. A task write and its replay succeeded with the worker stopped; no inbox/email arrived until restart. Recovery produced one assignment notification/email. Comment alerts, read state, email opt-out, Owner/Member audit and operations permissions, and scheduled reconciliation passed. Generic email content and the authenticated task link were checked.

During resumed verification Docker was stopped and restarted. Windows had an existing connection on local port 1025, so an untracked local Compose override mapped Mailpit SMTP to 11025. Container-to-container SMTP remains port 1025 and the project's default configuration is unchanged. Mailpit's browser inbox remains on 8025.

## Browser review

- A disposable Member account showed assignment and comment notifications, with no administrator navigation.
- Saving assignment preferences displayed confirmation; marking an item read changed the unread count from two to one.
- A notification link opened the correct task dialog.
- A disposable Owner account displayed audit records, including the preference/read actions, and Delivery & recovery showed zero pending events and a successful reconciliation timestamp.
- The recovery screen was inspected at 390 × 844: document width was 375 pixels (no horizontal overflow). Its navigation wrapped and controls remained visible. The viewport override was reset afterward.
- Desktop inbox screenshot: [notification inbox](screenshots/step8-notifications.jpg). Test credentials remain outside Git.

The hosted GitHub Actions workflow now includes the live activity check; these notes report local results, not an observed hosted CI result. SMTP can deliver duplicates after ambiguous acceptance despite a stable Message-ID. Retention, search, exports and deletion workflows remain Step 9, subject to user approval.
