# Release acceptance and evidence

Local acceptance evidence is separate from pilot deployment approval. No public deployment is authorized by completion of development checks.

| PRD gate | Evidence |
| --- | --- |
| AC-01: unrelated tenant IDs | Organization/project/collaboration/lifecycle negative tests; runtime missing-context denial across all 19 tables |
| AC-02: one user, different roles | test_cross_org_same_user_roles_and_composite_fk and role-matrix tests |
| AC-03: invitation race | test_acceptance_race |
| AC-04: ownership race | test_ownership_transfer_race |
| AC-05: concurrent task edit | test_concurrent_task_edit; exactly one winning version |
| AC-06: removed member | Immediate next-request revocation tests for team, project, files and exports |
| AC-07: worker/broker outage | Durable outbox tests and Step 8 real worker pause/recovery verification |
| AC-08: file safety/limits | Collaboration validation, scanner/quota tests and live ClamAV checks in Steps 7–9 |
| AC-09: export access | Lifecycle generation/download revocation tests and live private ZIP verification |
| AC-10: restore/purge | Step 10 isolated pg_restore/content/constraint/RLS checks; real synthetic object restore and scoped purge receipt |

The final local regression run after the policy/context optimization passed all 188 tests. See STEP-10-VERIFICATION.md for exact measurements and scope. The generated OpenAPI request/route contract is checked for source drift in CI; detailed response behavior remains in API.md and the feature guides.

## Pilot gates that require a deployment environment

- Re-run latency targets on the specified 2-vCPU/4-GB app host with separately documented DB resources and the intended providers. Local timings cannot certify that hardware target.
- Select region, budget, database, private object storage, outbound email and scanner operation. Restrict provider credentials and validate HTTPS/proxy/cookie settings.
- Configure and test daily encrypted backups, <=30-day expiry, independent deletion ledger and restore reconciliation. Meet measured RPO/RTO with the actual provider.
- Configure monitoring/alerts for API failure rate, latency, database saturation, queue age, exhausted jobs and backup failures.
- Complete a full assistive-technology/contrast/zoom audit against WCAG 2.2 AA.
- Confirm the latest hosted CI run succeeds and review dependency/security findings before pilot traffic. Local passing checks do not establish hosted CI success.

These gates are open, not silently waived. The local project can be demonstrated without making production availability, security-audit, accessibility-conformance or scale claims.
