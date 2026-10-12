# Projects and tasks

Step 6 adds persisted private projects and tasks to each organization. Use **Projects** in the workspace navigation. The overview now shows scoped project/task counts, recent projects and assigned open tasks. `/preview` remains a separate sample UI.

## Access and lifecycle

| Action | Owner / Admin | Member | Viewer |
| --- | --- | --- | --- |
| Read project and tasks | All organization projects | Explicit project membership | Explicit project membership |
| Create/update/archive/restore project | Yes | No | No |
| Add/remove project members | Yes | No | No |
| Create/update tasks | Yes | In accessible projects | No |
| Delete tasks | Yes | No | No |
| Receive an assignment | Explicit active project membership | Explicit active project membership | No |

Archived projects remain readable but reject task, settings and membership mutations. Restore rechecks active-project capacity. Organization-level offboarding is a system exception: it clears open assignments even in archived projects. Removing project membership or organization membership atomically removes project access, clears open assignments, increments affected task versions and captures audit/outbox events. A role change to Viewer clears open assignments but retains read access. Completed tasks retain historical attribution; reopening requires an eligible assignee or explicitly clearing it.

Task statuses are **To Do, In Progress, Blocked, Done**, as specified by the PRD. Any transition is allowed; entering Done sets `completed_at`, and leaving it clears that timestamp. A due date is a calendar date in the organization's timezone. A non-Done task is overdue when that date is earlier than the organization's current date.

Task deletion is versioned and soft: it hides the task immediately and releases its quota place. The retention target is 30 days; automated purge is deliberately scheduled for Step 9, so this stage retains deleted rows. There is no task restore or hard-delete UI in Step 6.

## API

All routes are below `/api/v1/orgs/{org_id}/`, require verified session authentication, and use CSRF protection for mutations. Hidden resources return 404; forbidden actions on visible resources return 403. Lists use signed filter-bound cursors, default 25 rows, maximum 100.

| Route | Methods | Purpose |
| --- | --- | --- |
| `projects/` | GET, POST | List accessible projects; create a private project |
| `projects/{id}/` | GET, PATCH | Project detail and name/description settings |
| `projects/{id}/archive/` | POST | Archive; repeated archive is safe |
| `projects/{id}/restore/` | POST | Restore if capacity allows |
| `projects/{id}/members/` | GET, POST | Project roster; add `membership_id` |
| `projects/{id}/members/{membership_id}/` | DELETE | Remove membership and clear open assignments |
| `projects/{id}/tasks/` | GET, POST | Filtered task list; create task |
| `tasks/{id}/` | GET, PATCH, DELETE | Task detail, optimistic update, soft deletion |
| `dashboard/` | GET | Counts/recent projects/my tasks from accessible active projects |

Project list filter: `state=active|archived|all`. Task filters: `status`, `priority`, `assignee=me|unassigned|{membership_id}`, and `overdue=true|false`. A cursor cannot be reused with different filters or another project.

Create project/task requests require `Idempotency-Key` (16–128 ASCII letters, numbers, underscores or hyphens). Successful responses are replayed for 24 hours, scoped to organization, actor and endpoint; changed payload reuse returns 409. The project creator receives explicit project membership.

Task fields: `title` (1–200 characters), `description` (up to 20,000), `status`, `priority` (Low/Medium/High/Urgent), nullable `due_date` (YYYY-MM-DD), nullable `assignee_id` (organization membership ID). Owner/Admin assignees must also be explicit project members. Task moves between projects/organizations are unsupported and payload identifiers are rejected.

PATCH requires integer `version` and at least one changed field. DELETE sends only `{"version": 1}` and returns 204. An atomic version predicate and increment prevent lost updates. Stale writes return 409 with `error.details.current_version`. The editor preserves the draft and offers an explicit discard/reload action. Board and list moves share the same versioned mutation; failed moves revert the optimistic card state and display an error.

## Database and consistency

- Four new tenant tables (`projects_project`, `projects_projectmembership`, `projects_task`, `projects_writerequest`) enable and force RLS. The runtime role is non-owner, non-superuser, NOBYPASSRLS and cannot TRUNCATE these tables. Missing tenant context returns no rows.
- RLS enforces the organization boundary; `accessible_projects` and related shared policy helpers enforce project visibility in the application, including detail, roster, tasks and dashboard routes. RLS alone does not enforce within-organization project membership.
- Deferred composite foreign keys prevent cross-organization project/member/task links and require task assignees to have an explicit project membership record. Removed project membership rows remain for historical completed-task attribution. The service layer additionally checks active status and role.
- Mutations lock the organization's usage row, recheck membership and update data, usage, audit and outbox in the same transaction. This serializes quota decisions, archive/write races and access-removal cleanup. Task updates also use a version predicate.
- Defaults: 20 active projects and 10,000 nondeleted tasks. New organizations use `ORG_PROJECT_LIMIT` and `ORG_TASK_LIMIT`; existing usage rows retain their configured limits. Archived tasks still consume task capacity. Limits are independent of frontend controls.
- Task writes do not call SMTP or publish to a broker. General notification delivery is Step 8; this stage durably captures the events. Audit payloads contain IDs, changed field names and versions, not task text.

## Local verification

Run the PostgreSQL test suite, `ruff check .`, `ruff format --check .`, Django checks and frontend build. With local services running:

```sh
python backend/verify_projects.py --base-url http://127.0.0.1:5174
docker compose exec -T web python verify_tenant_security.py
```

The HTTP check creates disposable local accounts, verifies them through Mailpit, and checks real runtime permissions and project/task flows. Optional `--ui-account-file` writes review credentials; always choose a scratch path outside Git. See [Step 6 results](STEP-6-VERIFICATION.md).
