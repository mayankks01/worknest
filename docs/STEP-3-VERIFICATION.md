# Step 3 verification

Date: 11 October 2026. Scope: accounts and authentication only.

## Result

Registration, email verification/resend, login/logout, password recovery, account status, and the matching navy/blue screens are implemented. The custom user migration was applied to the existing local foundation database without resetting its volume. Step 4 has not begun.

## Checks performed

| Check | Observed result |
| --- | --- |
| Backend suite | 43 tests passed against PostgreSQL and Redis in the final full run, including the corrected broker-publication failure test. |
| Identity | Normalized email uniqueness; strong password validation; field allowlist; generic duplicate registration/recovery responses. |
| CSRF | Anonymous and authenticated POSTs require a token; forged origins fail; login rotates the token. |
| Sessions | Key rotation, absolute 12-hour expiry, logout, inactive-user denial, and password-reset invalidation across two sessions. |
| Token lifecycle | Verification, replay rejection, tampering, purpose mismatch, expiry, replacement on resend, and reset race with one success and one rejection. |
| Rate limits | Real Redis limits by IP/email; concurrent requests cannot exceed the configured allowance; Redis failure returns 503. |
| Email job logic | Delivered jobs are not replayed; retry/backoff and five-attempt failure; consumed links skipped; expired worker leases and missed publication recovered. |
| Live HTTP integration | Through Vite on port 5174 to Django using the restricted application DB role: registration → Celery SMTP delivery to Mailpit → verification → login → reset email → password reset → both old sessions rejected → new login → logout. |
| Frontend | TypeScript check and Vite production build passed. Desktop login reviewed at 1440 px; mobile registration reviewed at 390 px with no page overflow. |
| Browser interactions | Required-field validation focuses the name field; recovery displays generic success; malformed verification link shows a safe error; token fragment removed from address bar. No console errors in the reviewed tab. |
| Backend quality | Ruff checks/formatting passed. Django system check passed; migration consistency reported no changes. |

## Review

- [Desktop sign-in](accounts-login.jpg)
- [Mobile registration](accounts-mobile.jpg)
- [API and security decisions](ACCOUNTS.md)

## Practical limits

- Mailpit captures local email; external delivery is not configured or tested.
- HTTP smoke tests exercise complete account flows. Browser review covered layout/navigation/validation/recovery feedback, not entry of passwords through the browser automation tool.
- A disposable `worknest-smoke-…@example.test` account and its test emails remain in the local development stack. The automated backend suite used a separate temporary test database.
- Deployment HTTPS/proxy behavior and hosted GitHub Actions have not been verified in this stage. CI configuration includes the account suite and live email smoke check. Hosted CI status must be checked separately from these local results.
- Organization creation/join routes do not exist yet. A tested verified-email permission is ready to be applied when those routes are implemented. Tenant isolation and forced RLS remain Step 4.
- The dashboard at `/preview` continues to use clearly labeled sample data.

Next approval: Step 4 — organizations, memberships, request-scoped tenant context, and database isolation.

## Local service recovery before push

Docker had stopped before final verification. Redis reported a corrupt 770-byte tail in its append-only persistence log. The original directory was copied to /data/appendonly-backup-step3-20261011 in the existing Redis volume before redis-check-aof repaired the tail. PostgreSQL data was not reset. Services restarted, and the full 43-test suite passed again. The frontend was restarted on port 5174.
