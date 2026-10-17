# API guide

[OpenAPI 3.0 contract](openapi.json) inventories every implemented route, method, accepted body field, cookie security and mutation header. Generate it with `python backend/generate_openapi.py`; `--check` fails if the checked-in artifact is stale. The contract intentionally uses broad success responses; the resource examples and validation rules below and the module guides remain necessary for client implementation. It is not a complete generated SDK schema.

## Session and errors

Use the same origin as the web client. GET `/api/v1/auth/session/` returns the CSRF token and current session information. Include its cookies and `X-CSRFToken` on POST/PATCH/DELETE, including login. Login rotates session/CSRF state; fetch the current token after login. Cookies are HTTP-only session credentials; do not put them in URLs or local storage. Production requires HTTPS.

JSON bodies reject unknown fields. Auth text fields are strings; task/comment versions are integers; preference/read flags are booleans. Send an empty JSON object for actions with no fields. Use a unique 16–128-character Idempotency-Key for organization/project/task/invitation/export creation and attachment upload. Reuse it only for retries of the same payload.

Errors use `{"error":{"code":"...","message":"...","request_id":"...","details":{}}}`. Common statuses: 400 invalid input, 401 signed out, 403 disallowed visible action or CSRF failure, 404 hidden/missing resource, 409 stale version/quota/archive/conflict, 429 throttle, and 503 unavailable dependency. Inspect code/details instead of parsing messages. Every response has an X-Request-ID and no-store cache policy.

## Resources and pagination

Organization creation returns 201 with `organization`; project creation returns 201 with `project`; task creation returns 201 with `task`. Tasks include id, project_id, title, description, status, priority, version, assignee_id, due_date and completion metadata. PATCH a task with its current version; a competing update returns 409 with current_version. DELETE a task also requires a JSON version. Comments have independent versions. Collection responses use `results` and `next_cursor`; pass the opaque cursor unchanged. Search additionally returns count. Never reuse a cursor between organizations, users or filter sets.

Project lists accept state active/archived/all. Task lists accept status, priority, assignee (UUID/me/unassigned), overdue (true/false), limit and cursor. Search accepts q (2–200 characters), state, optional project, limit and cursor. Notifications accept unread true/false and cursor/limit. Failed operations accept kind event/email/file/reconcile. Audit is a read-only Owner/Admin collection. Page limits are validated server-side; use the default unless the module guide specifies otherwise.

## Uploads and lifecycle

Uploads use multipart/form-data with one `file` part and an idempotency key; accepted uploads return 202 while quarantined. Do not download until state available. Downloads are authenticated binary streams, never public object URLs. See [collaboration](COLLABORATION.md) for formats and limits.

Exports return 202 with an export job; inspect its state and use download_url when ready. Requests and downloads require current Owner/Admin access, and only the requester can retrieve that export. The Owner schedules or cancels deletion with `action`, current `password`, and `confirmation` equal to the organization slug. See [lifecycle](LIFECYCLE.md).

## Permission matrix

| Capability | Owner | Admin | Member | Viewer |
| --- | --- | --- | --- | --- |
| Read organization | Yes | Yes | Yes | Yes |
| Read projects/tasks/comments/files | All projects | All projects | Explicit project membership | Explicit project membership |
| Create/edit tasks and collaborate | Yes | Yes | Accessible unarchived projects | No |
| Manage projects/membership | Yes | Yes | No | No |
| Manage organization team | Yes | Restricted (cannot manage Owner/Admin) | No | No |
| Audit/recovery/export | Yes | Yes | No | No |
| Transfer ownership/schedule deletion | Yes, reauthenticate | No | No | No |

Archive and deleted-organization deadlines further restrict these rights. Author-specific comment/file rules are documented in [collaboration](COLLABORATION.md); complete team rules are in [team administration](TEAM.md). Authorization always uses current membership, not UI visibility.
