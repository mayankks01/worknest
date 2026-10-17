# Step 10 verification — 17 October 2026

**Local release-readiness work is implemented; pilot release remains blocked by the gates below.** This report does not mark unmet targets as passed.

## Changes and correctness

Added guarded synthetic seed tooling, a repeatable isolated migration/backup/restore/purge drill, a 50-client HTTP benchmark, OpenAPI route/request inventory with CI drift checking, recovery instructions, a permissions guide and a five-minute demo. Keyboard review added skip links, arrow/Home/End project tabs, dialog focus restoration and reduced-motion support.

Profiling found per-row repetition of transaction-scoped permission helpers during task searches. The new reversible migration evaluates the same STABLE, zero-argument role/organization helpers once per SQL statement on project policies. It retains the organization predicate and all background policies; no permission result survives a statement. Tenant context setup/restoration is batched into three SQL calls instead of a separate round trip per setting. Optional bounded connection reuse defaults to disabled and enables health checks when used.

Full regression suite after the migration/context changes: **188 passed in 239.53 seconds**. This includes cross-tenant reads/writes, role changes, immediate revocation, nested transaction rollback, quota races, outbox recovery, private files, export authorization and purge boundaries. An additional prepared-query/revocation test and the existing nested-context test also passed with connection reuse enabled: **2 passed in 8.32 seconds**. There are 189 current cases covered across these runs; no single 189-case run is claimed. TypeScript/Vite production build, Ruff and generated OpenAPI drift checks passed. No hosted CI success is inferred from local results.

## Recovery evidence

A newly migrated database with two unrelated organizations and 16 synthetic tasks was dumped and restored to a separate new database. Task count/content hash matched; all constraints were validated; all 19 tenant tables still forced RLS under the actual non-owner runtime role. Real synthetic object bytes were backed up, deleted, restored and verified, then the restored organization's overdue purge removed its objects and tenant rows and recorded completion. A neighboring object's bytes remained intact. The initial combined restore/purge run measured pg_restore at **1.793 seconds**.

The larger 100-organization/100,000-task database restored in **4.551 seconds**, with matching task contents, validated constraints and runtime RLS. This larger run preceded the policy optimization; a fresh small drill repeated the final migration graph and passed restore/object-purge checks, with pg_restore taking **2.176 seconds**. Times measure pg_restore only, not full disaster recovery, encrypted backup transport, provider provisioning or production RTO. See RECOVERY.md for the full procedure and required deletion-ledger replay.

## Performance: measured, target not met

Dataset: 100 unrelated organizations, one owner and one project each, 1,000 tasks per organization (100,000 total). Fifty concurrent closed-loop HTTP clients performed 1,000 scoped search reads and 1,000 task PATCH writes over ten rounds per user. Authentication sessions were pre-established; upload/export and login are excluded. There is no think time or separate warm-up phase. The repeatable harness fetches current task versions before timing, preventing a retry run from generating artificial version conflicts. Response bodies are consumed before stopping the timer.

Environment: Windows with Docker Desktop Linux/WSL2, Docker engine 29.8.1; Docker reported 12 logical CPUs and 3,918,733,312 bytes total memory shared by services. Review app container limited to **2 CPUs, 1 GiB RAM, 2 GiB memory+swap**, Gunicorn 2 workers × 25 threads. PostgreSQL 17.11 shared the Docker VM without a separate CPU/memory cap; Redis, ClamAV and storage also ran there. This is not the PRD's dedicated 2-vCPU/4-GB application host with separately provisioned database. Earlier full-suite work overlapped part of baseline preparation; compare these runs as local diagnostics, not controlled lab results.

| Run | Read p50 / p95 | Write p50 / p95 | Errors | Total |
| --- | --- | --- | --- | --- |
| Baseline | 10,818 / 23,652 ms | 2,036 / 6,834 ms | 0 read, 6 write | 307.85 s |
| Statement checks + batched context | 1,483 / 2,487 ms | 2,365 / 3,469 ms | 0 / 2,000 | 86.87 s |
| Same changes + 60-second connection reuse | 1,067 / 1,586 ms | 1,814 / 2,433 ms | 0 / 2,000 | 64.01 s |

Raw aggregates are in benchmarks/. The baseline harness did not retain error status distribution; the updated harness does. No claim is made about the baseline errors' cause. The representative raw scoped task query after optimization executed in 21.855 ms on an otherwise idle review run; it is not an HTTP latency measurement and cannot be compared directly with the loaded baseline query.

The PRD requires p95 reads <300 ms and writes <500 ms at 50 concurrent users. **Neither target passed.** Next performance work must profile the remaining request/DB planning costs and retest on the agreed host. Do not advertise production capacity from these results.

Reproduce using `python backend/release_review.py --output ../review-load --tenants 100 --tasks 1000 --benchmark`. The default uses fresh connections. For the optional reuse experiment, run the isolated review app with DATABASE_CONN_MAX_AGE=60 and rerun benchmark_review.py against its source sessions file. Keep sessions.json outside Git. The benchmark server is stopped after review; synthetic databases/dumps remain for inspection.

## Accessibility and release decision

See ACCESSIBILITY.md for observed keyboard/focus checks and the screenshot. Full screen-reader, contrast, zoom and target-size coverage remain open. See RELEASE-CHECKLIST.md for AC-01–AC-10 evidence and outstanding provider-backed backup, monitoring, security review, hosting and CI gates. The demo and local tests can be shown; production readiness, performance compliance and WCAG conformance cannot yet be claimed.
