from contextlib import contextmanager

from django.db import connection, transaction


@contextmanager
def tenant_transaction(
    actor_id,
    org_id=None,
    *,
    discovery=False,
    invitation_hash="",
    email_event="",
    file_id="",
    activity_id="",
):
    """Only trusted server identities/URL IDs enter here, never body-supplied context.

    set_config(..., true) is parameterized SET LOCAL. Explicitly restore the prior
    values for nested callers; PostgreSQL also clears them on commit/rollback.
    Workers must enter this boundary explicitly, just like HTTP requests.
    """
    values = {
        "worknest.activity_id": str(activity_id) if activity_id else "",
        "worknest.file_id": str(file_id) if file_id else "",
        "worknest.actor_id": str(actor_id) if actor_id else "",
        "worknest.org_id": str(org_id) if org_id else "",
        "worknest.discovery": "on" if discovery else "off",
        "worknest.invitation_hash": invitation_hash,
        "worknest.email_event": str(email_event) if email_event else "",
    }
    with transaction.atomic():
        previous = {}
        with connection.cursor() as cursor:
            for key, value in values.items():
                cursor.execute("SELECT current_setting(%s, true)", [key])
                previous[key] = cursor.fetchone()[0] or ""
                cursor.execute("SELECT set_config(%s, %s, true)", [key, value])
        try:
            yield
        finally:
            if not connection.needs_rollback:
                with connection.cursor() as cursor:
                    for key, value in previous.items():
                        cursor.execute("SELECT set_config(%s, %s, true)", [key, value])
