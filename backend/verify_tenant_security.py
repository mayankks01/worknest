"""Run inside the web container to verify the real runtime database role."""

import os

import django
from django.db import connection

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

with connection.cursor() as cursor:
    cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    assert cursor.fetchone() == (False, False), "Runtime role must not bypass RLS"
    cursor.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "pg_get_userbyid(c.relowner) = current_user "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND "
        "(c.relname LIKE 'organizations_%' OR c.relname LIKE 'projects_%' "
        "OR c.relname LIKE 'collab_%' OR c.relname LIKE 'activity_%') "
        "AND c.relkind = 'r'"
    )
    tables = cursor.fetchall()
    assert len(tables) == 17, (
        "Expected all seventeen organization, team, project and collaboration tables"
    )
    for name, enabled, forced, owned in tables:
        assert enabled and forced and not owned, f"Unsafe table policy/ownership: {name}"
        # Names come only from the catalog and are quoted by the backend.
        cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(name)}")
        assert cursor.fetchone()[0] == 0, f"Missing context must hide {name}"
        cursor.execute("SELECT has_table_privilege(current_user, %s, 'TRUNCATE')", [name])
        assert not cursor.fetchone()[0], f"Runtime role must not truncate {name}"
print(
    "PASS: actual runtime role is non-owner, non-superuser, NOBYPASSRLS; "
    "all seventeen tables force RLS, deny TRUNCATE and fail closed without context."
)
