from django.db import migrations


def policies(cached):
    org = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
    actor = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
    role = "public.worknest_current_role()"
    active = "public.worknest_org_active()"
    if cached:
        # These STABLE, zero-argument helpers depend only on transaction context and
        # the statement snapshot, never the candidate row. InitPlans avoid repeating
        # membership lookups for every task. No value survives the SQL statement.
        role, active = f"(SELECT {role})", f"(SELECT {active})"
    scope = f"org_id={org} AND {role} IS NOT NULL AND {active}"
    sql = ""
    for table, roles in [
        ("project", "'Owner','Admin'"),
        ("projectmembership", "'Owner','Admin'"),
        ("task", "'Owner','Admin','Member'"),
    ]:
        write = f"{scope} AND {role} IN ({roles})"
        sql += f"ALTER POLICY {table}_read ON projects_{table} USING ({scope});\n"
        sql += f"ALTER POLICY {table}_create ON projects_{table} WITH CHECK ({write});\n"
        sql += (
            f"ALTER POLICY {table}_update ON projects_{table} "
            f"USING ({write}) WITH CHECK ({write});\n"
        )
    writer = f"{scope} AND {role} IN ('Owner','Admin','Member') AND actor_id={actor}"
    return sql + (
        "ALTER POLICY project_request_scope ON projects_writerequest "
        f"USING ({writer}) WITH CHECK ({writer});"
    )


class Migration(migrations.Migration):
    dependencies = [("projects", "0002_tenant_security"), ("lifecycle", "0002_security")]
    operations = [migrations.RunSQL(policies(True), policies(False))]
