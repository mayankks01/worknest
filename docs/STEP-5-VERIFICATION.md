# Step 5 verification

Verified locally on 12 October 2026. Scope: team administration only; Step 6 awaits user approval.

- Full PostgreSQL/Redis backend suite: **95 passed**. After final invitation throttling changes, all **28 team tests passed** again.
- Ruff checks and formatting, Django system checks and migration drift checks passed. TypeScript check and Vite production build passed.
- Restricted-role tests cover role hierarchy, Owner protection, wrong/unverified emails, expiration/revocation, resends, idempotency, single-use races, final-place quota races, ownership-transfer races, cross-tenant denial, immediate revocation, rejoin, pagination, atomic rollback, email retries and worker scope.
- The live migration was applied to the existing local database under the non-superuser owner role. Existing-organization backfill and worker-function creation issues found during verification were corrected before delivery.
- The HTTP smoke passed using the actual application role: registration and verification through Mailpit, invitation email, private preview, CSRF rejection, accept/replay, role update, password-confirmed transfer, removal and rejoin.
- The real runtime role is non-owner, non-superuser and NOBYPASSRLS. All eight organization/team tables force RLS, deny TRUNCATE and return zero rows without tenant context.
- Browser review confirmed organization navigation, roster/quota display, invitation submission and sent status, and the password-confirmation dialog. Desktop checked at 1440 CSS pixels; mobile at 390 CSS pixels. Mobile table overflow was contained within focusable cards, with no page-wide horizontal overflow.
- Browser invitation review and acceptance returned to the Members screen; an existing Owner kept their role. The email test selects the exact invitation ID to avoid reopening older consumed links.
- Local review accounts and credentials are outside Git. Screenshots contain disposable test identities only.

## Boundaries

Email is captured in local Mailpit, not delivered externally. SMTP is at least once; the invitation itself remains single-use. Project/task offboarding cleanup will be added in Step 6 when those records exist. General notification processing remains Step 8. CI includes the live team smoke, but local results do not claim a hosted CI run passed.

See [team behavior and API](TEAM.md), [desktop screenshot](team-desktop.jpg), and [mobile screenshot](team-mobile.jpg).
