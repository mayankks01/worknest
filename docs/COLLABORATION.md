# Comments and private attachments (Step 7)

Open an existing task to use **Discussion & files**. Comments are plain text (1–5,000 characters), shown oldest first with cursor pagination. Authors can edit their own comments; Owner/Admin can delete any comment and Members their own. Edits/deletions require the current version and return 409 on a stale version. Viewers and archived projects are read-only. Deleted tasks immediately hide their discussions and files.

## File flow

One multipart file per request: PDF, PNG, JPEG or UTF-8 TXT. Defaults are 10 MiB per file and 1 GiB reserved storage per organization (`ATTACHMENT_MAX_BYTES`, `ORGANIZATION_STORAGE_LIMIT`). Changing the latter sets the default for newly created organizations; existing limits live in their usage rows. The upload request's content type is not trusted. Filenames are normalized and cannot control object paths.

1. Authenticate and check live organization/project access. Reserve bytes under the shared organization usage lock; create a durable file record and audit/outbox events in the same transaction. Upload retries require an `Idempotency-Key` (24-hour replay window).
2. Write bytes to a private, random object key outside database transactions. Metadata stays unavailable for download while queued or scanning.
3. A scoped Celery worker rechecks the uploader's access, reads the expected size/SHA-256, sends bytes to real ClamAV, and validates the file structure. Only a definitive clean result plus successful format validation makes a file available. Scanner errors fail closed.
4. Authenticated downloads recheck current access both before and after fetching storage. The response forces a download with `nosniff`, a restrictive CSP and no-store caching. No object keys or reusable public URLs are returned.
5. Failed/rejected/deleted objects enter cleanup. Reserved bytes are released only after successful object deletion, once. A deletion supersedes any in-flight scan result.

PNG/JPEG parsing limits image dimensions to 20 million pixels. PDFs must be unencrypted, contain 1–500 pages and have no forms, annotations, JavaScript, opening actions or embedded files. TXT must be nonempty UTF-8 with no unsupported control characters. These restrictions complement scanning; they are not a guarantee that every possible harmful document is detectable.

## API

All paths are under `/api/v1/orgs/{org_id}/` and use same-origin sessions, CSRF and the standard error envelope.

| Path | Methods | Purpose |
| --- | --- | --- |
| `tasks/{task_id}/comments/` | GET, POST | List/create plain-text comments; POST requires an idempotency key |
| `comments/{comment_id}/` | PATCH, DELETE | Edit/delete with `version` |
| `tasks/{task_id}/attachments/` | GET, POST | List metadata/storage usage; multipart `file` upload with idempotency key |
| `attachments/{attachment_id}/` | GET, PATCH, DELETE | Read current state, rename one's available file (same extension), or delete |
| `attachments/{attachment_id}/download/` | GET | Stream a checked file after current access checks |

Lists accept `limit` (1–100, default 25) and a signed `cursor`. Hidden resources return 404, forbidden actions 403, unready downloads 409 and capacity conflicts 409. Owner/Admin can delete others' files; only the uploader can rename. Rejected files remain visible as explanatory metadata until removed.

## Local services and recovery

```powershell
docker compose up -d --build
docker compose ps scanner
docker compose run --rm storage-init
python backend/verify_collaboration.py --base-url http://localhost:5173
docker compose exec -T web python verify_tenant_security.py
```

ClamAV uses the pinned official image, a persistent signature volume and a 4 GiB memory limit. Allow time for signatures and the initial scanner startup. Signature downloads require internet access; a scanner that cannot initialize leaves uploads unavailable. Native development uses `CLAMAV_HOST=localhost`, port 3310; Compose uses service name `scanner`.

The durable database job record is redispatched by beat every 15 seconds. Claims have a five-minute lease; interrupted uploads expire after 15 minutes. Scan and cleanup retries use exponential delays with jitter, capped at five attempts. Storage network calls do not hold database business locks. Exhausted cleanup retains quota and requires an operator to repair storage and run:

```powershell
docker compose exec -T web python manage.py retry_file_cleanup <organization-uuid> <file-uuid>
```

This command only retries exhausted cleanup for the exact pair; it records an audit/outbox event and never releases quota itself. Failed scans can be removed and uploaded again after the service is repaired. Reconciliation/failed-job dashboards belong to Step 8; task retention/purge belongs to Step 9. A process killed after a very late object write can require future orphan reconciliation. Pilot release still requires provider-specific bucket policy, scanner freshness/availability monitoring and the later release checks.

Two new tables force tenant RLS and composite organization/task/author relationships. Narrow fixed-search-path helpers provide exact file routing and metadata for background work; the runtime database user remains non-owner and cannot bypass RLS. Audit/outbox summaries contain identifiers and state/reason codes, never comment bodies, filenames or file bytes.

The scanner adapter follows the official [ClamAV protocol](https://docs.clamav.net/manual/Usage/ClamdProtocol.html), and local scanner provisioning follows the [ClamAV Docker documentation](https://docs.clamav.net/manual/Installing/Docker.html). See [verification evidence](STEP-7-VERIFICATION.md).
