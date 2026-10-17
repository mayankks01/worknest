# Five-minute portfolio demonstration

Use the README to start a fresh local Compose environment. Mail remains in Mailpit. The preview at /preview is design-only; demonstrate real accounts under /orgs.

For deterministic synthetic data, run `python backend/release_review.py --output ../demo-review`. This creates two unrelated organizations in a separate review database and retains their IDs in a local sessions file. Seed accounts have unusable passwords by default. To prepare an interactive demo in a separate empty worknest_review_* database, migrate it as documented in the drill and supply REVIEW_PASSWORD through a private environment variable to `python manage.py seed_review --tenants 2 --tasks 8`. Do not commit or show the password/session file. Point a separate loopback app at that database; never merge synthetic records into a real tenant database.

Alternatively, reproduce the full account/invitation flow with the existing local verification scripts listed in README and the step verification documents. `verify_activity.py --pause-worker --ui-account-file PATH` creates disposable review accounts, pauses/restarts the local worker and saves credentials outside Git. Run this only in the local demonstration stack. `verify_lifecycle.py --account-file PATH` adds the export/cancellation demonstration.

| Time | Show | Observable result |
| --- | --- | --- |
| 0:00–0:45 | Sign in, create organization A and open its projects | Real account/tenant data, navy/blue workspace |
| 0:45–1:30 | Invite a contributor, accept via Mailpit and add project access | Contributor initially cannot see the private project; membership grants access |
| 1:30–2:15 | Create, assign and complete a task using list status control | Versioned task update, progress and assignment notification |
| 2:15–3:00 | Sign in as unrelated organization B's owner; attempt A's saved task URL | 404 with no task metadata; B's workspace remains visible |
| 3:00–3:45 | Open the same task in two tabs and save conflicting versions | Second stale save returns a conflict; reload preserves the winning edit |
| 3:45–4:30 | Pause local worker, assign a task, then resume worker | Task is committed during outage; queued notification completes once |
| 4:30–5:00 | Show private export, passing restore report and measured benchmark | Evidence and limitations are visible; no unsupported production claims |

The user-facing demo should not display session files, passwords, private storage keys or raw database credentials. Pilot deployment is a separate decision requiring hosting, region, providers and budget.
