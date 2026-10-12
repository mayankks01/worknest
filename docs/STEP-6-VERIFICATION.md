# Step 6 verification

Verified locally on 12 October 2026. Scope: projects and tasks. Step 7 requires separate user approval.

- **125 backend tests passed**, including 30 new project/task tests on PostgreSQL. These cover role/project visibility, cross-tenant denial, composite relationships, task validation, assignee eligibility, Done/reopen timestamps, stale edits and concurrent updates, project/task quota races, archive/restore, atomic rollback, idempotency, filters/cursors, organization-local overdue dates, CSRF, soft deletion and offboarding.
- Ruff checks/formatting, Django system checks and migration drift checks passed. TypeScript checking and Vite production build passed.
- Existing local database migration succeeded through the non-superuser owner role. The actual running web role is non-owner, non-superuser, NOBYPASSRLS. All **12 tenant tables** force RLS, deny TRUNCATE and hide every row without tenant context.
- `verify_projects.py` passed through the live same-origin frontend proxy and actual restricted database role: verified accounts, project creation/replay, project privacy, membership grant/revocation, Member task writes, CSRF, stale 409 response, archive/restore, open-task unassignment, dashboard and versioned soft deletion.
- Browser checks confirmed project creation, task creation, assignee selection, native keyboard date entry and persisted due dates, drag-and-drop persistence, list status controls, archive/read-only controls and restore. A controlled second-client edit caused a failed move to revert. A separate stale editor retained its unsaved draft and disabled saving until explicit reload.
- Desktop reviewed at **1440 × 1000 CSS pixels** and mobile at **390 × 844**. A progress-header overflow found during review was fixed. No page-wide horizontal overflow remained; the mobile board scrolls within its own container. The list provides an alternative to dragging.
- Review accounts, passwords and conflict-driving scripts remain outside Git. Screenshots show disposable test records only. The CI workflow includes the new live project smoke; local results do not claim a hosted CI run passed.

## Boundaries

This is a local development build. Mail remains local to Mailpit. Comments/uploads are Step 7, notification delivery is Step 8, and automated 30-day task purge plus broader search/export lifecycle are Step 9. No production load/performance or deployment claim is made. Project visibility within a tenant is an application policy; RLS independently protects the organization boundary.

See [behavior and API](PROJECTS.md), [desktop board](projects-desktop.jpg), [mobile board](projects-mobile.jpg), and [organization dashboard](projects-dashboard.jpg).
