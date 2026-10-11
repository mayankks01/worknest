from django.db import migrations

ACTOR = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
ORG = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
DISCOVERY = "current_setting('worknest.discovery', true) = 'on'"
VERIFIED = f"""EXISTS (SELECT 1 FROM accounts_user u
    WHERE u.id = {ACTOR} AND u.is_active AND u.verified_at IS NOT NULL)"""


def membership(table, roles=""):
    return f"""EXISTS (SELECT 1 FROM organizations_membership m
        JOIN accounts_user u ON u.id = m.user_id
        WHERE m.org_id = {table}.id AND m.user_id = {ACTOR}
        AND m.state = 'active' AND u.is_active {roles})"""


def tenant_member(table):
    return f"""{table}.org_id = {ORG} AND EXISTS (
        SELECT 1 FROM organizations_membership m JOIN accounts_user u ON u.id = m.user_id
        WHERE m.org_id = {table}.org_id AND m.user_id = {ACTOR}
        AND m.state = 'active' AND u.is_active)"""


TABLES = ["organization", "membership", "auditevent", "outboxevent", "creationrequest"]
SQL = "\n".join(
    f"ALTER TABLE organizations_{table} ENABLE ROW LEVEL SECURITY;\n"
    f"ALTER TABLE organizations_{table} FORCE ROW LEVEL SECURITY;"
    for table in TABLES
)
SQL += f"""
-- Discovery is the only deliberate cross-organization read: the actor's own
-- memberships and their active organization metadata, never tenant content.
CREATE POLICY membership_self ON organizations_membership FOR SELECT USING (
    user_id = {ACTOR} AND (org_id = {ORG} OR {DISCOVERY}) AND state = 'active'
);
CREATE POLICY membership_bootstrap ON organizations_membership FOR INSERT WITH CHECK (
    org_id = {ORG} AND user_id = {ACTOR} AND role = 'Owner' AND state = 'active'
    AND {VERIFIED}
);
CREATE POLICY org_visible ON organizations_organization FOR SELECT USING (
    (id = {ORG} OR {DISCOVERY}) AND state = 'active'
    AND {membership("organizations_organization")}
);
CREATE POLICY org_create ON organizations_organization FOR INSERT WITH CHECK (
    id = {ORG} AND created_by_id = {ACTOR} AND state = 'active' AND {VERIFIED}
);
CREATE POLICY org_settings ON organizations_organization FOR UPDATE USING (
    id = {ORG} AND state = 'active'
    AND {membership("organizations_organization", "AND m.role IN ('Owner', 'Admin')")}
) WITH CHECK (
    id = {ORG} AND state = 'active'
    AND {membership("organizations_organization", "AND m.role IN ('Owner', 'Admin')")}
);
CREATE POLICY audit_read ON organizations_auditevent FOR SELECT USING (
    {tenant_member("organizations_auditevent")}
);
CREATE POLICY audit_append ON organizations_auditevent FOR INSERT WITH CHECK (
    {tenant_member("organizations_auditevent")} AND EXISTS (
        SELECT 1 FROM organizations_membership m
        WHERE m.id = actor_membership_id AND m.user_id = {ACTOR}
        AND m.org_id = organizations_auditevent.org_id AND m.state = 'active'
    )
);
CREATE POLICY outbox_read ON organizations_outboxevent FOR SELECT USING (
    {tenant_member("organizations_outboxevent")}
);
CREATE POLICY outbox_append ON organizations_outboxevent FOR INSERT WITH CHECK (
    {tenant_member("organizations_outboxevent")}
);
CREATE POLICY request_self ON organizations_creationrequest USING (actor_id = {ACTOR})
    WITH CHECK (actor_id = {ACTOR});

-- Cyclic deferred FK + unique active Owner guarantee exactly one Owner at
-- commit, even for raw SQL. Referential checks intentionally bypass RLS.
ALTER TABLE organizations_organization ADD CONSTRAINT org_active_owner_fk
    FOREIGN KEY (id, owner_membership_id, owner_role, owner_state)
    REFERENCES organizations_membership (org_id, id, role, state)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE organizations_auditevent ADD CONSTRAINT audit_actor_same_org_fk
    FOREIGN KEY (org_id, actor_membership_id)
    REFERENCES organizations_membership (org_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE organizations_outboxevent ADD CONSTRAINT outbox_audit_same_org_fk
    FOREIGN KEY (org_id, audit_id)
    REFERENCES organizations_auditevent (org_id, id) DEFERRABLE INITIALLY DEFERRED;
"""

REVERSE = """
ALTER TABLE organizations_outboxevent DROP CONSTRAINT outbox_audit_same_org_fk;
ALTER TABLE organizations_auditevent DROP CONSTRAINT audit_actor_same_org_fk;
ALTER TABLE organizations_organization DROP CONSTRAINT org_active_owner_fk;
"""
for table, policies in {
    "organization": ["org_visible", "org_create", "org_settings"],
    "membership": ["membership_self", "membership_bootstrap"],
    "auditevent": ["audit_read", "audit_append"],
    "outboxevent": ["outbox_read", "outbox_append"],
    "creationrequest": ["request_self"],
}.items():
    REVERSE += "\n".join(f"DROP POLICY {p} ON organizations_{table};" for p in policies)
    REVERSE += f"\nALTER TABLE organizations_{table} DISABLE ROW LEVEL SECURITY;\n"


class Migration(migrations.Migration):
    dependencies = [("organizations", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
