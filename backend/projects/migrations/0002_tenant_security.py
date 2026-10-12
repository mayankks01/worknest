from django.db import migrations

ORG = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
ACTOR = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
ROLE = "public.worknest_current_role()"
SCOPE = f"org_id={ORG} AND {ROLE} IS NOT NULL AND public.worknest_org_active()"
MANAGER = f"{SCOPE} AND {ROLE} IN ('Owner','Admin')"
WRITER = f"{SCOPE} AND {ROLE} IN ('Owner','Admin','Member')"

SQL = ""
for table, write in [("project", MANAGER), ("projectmembership", MANAGER), ("task", WRITER)]:
    name = "projects_" + table
    SQL += f"""
ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {name} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_read ON {name} FOR SELECT USING ({SCOPE});
CREATE POLICY {table}_create ON {name} FOR INSERT WITH CHECK ({write});
CREATE POLICY {table}_update ON {name} FOR UPDATE USING ({write}) WITH CHECK ({write});
"""
SQL += f"""
ALTER TABLE projects_writerequest ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects_writerequest FORCE ROW LEVEL SECURITY;
CREATE POLICY project_request_scope ON projects_writerequest
USING ({WRITER} AND actor_id={ACTOR}) WITH CHECK ({WRITER} AND actor_id={ACTOR});

ALTER TABLE projects_projectmembership ADD CONSTRAINT pm_project_same_org_fk
FOREIGN KEY (org_id,project_id)
REFERENCES projects_project(org_id,id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE projects_projectmembership ADD CONSTRAINT pm_member_same_org_fk
FOREIGN KEY (org_id,membership_id)
REFERENCES organizations_membership(org_id,id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE projects_task ADD CONSTRAINT task_project_same_org_fk
FOREIGN KEY (org_id,project_id)
REFERENCES projects_project(org_id,id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE projects_task ADD CONSTRAINT task_creator_same_org_fk
FOREIGN KEY (org_id,created_by_id)
REFERENCES organizations_membership(org_id,id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE projects_task ADD CONSTRAINT task_assignee_project_fk
FOREIGN KEY (org_id,project_id,assignee_id)
REFERENCES projects_projectmembership(org_id,project_id,membership_id)
DEFERRABLE INITIALLY DEFERRED;

-- Task creation by a Member locks and updates the shared quota row too.
DROP POLICY usage_update ON organizations_organizationusage;
CREATE POLICY usage_update ON organizations_organizationusage FOR UPDATE USING (
    org_id={ORG} AND ({ROLE} IN ('Owner','Admin','Member')
    OR public.worknest_invited_role() IS NOT NULL)
) WITH CHECK (org_id={ORG});
"""


class Migration(migrations.Migration):
    dependencies = [("projects", "0001_initial")]
    operations = [migrations.RunSQL(SQL)]
