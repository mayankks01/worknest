from django.db import migrations

A = "NULLIF(current_setting('worknest.actor_id',true),'')::uuid"
ORG = "NULLIF(current_setting('worknest.org_id',true),'')::uuid"
J = "NULLIF(current_setting('worknest.lifecycle_id',true),'')::uuid"
OWNER = """
current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE
oid='public.lifecycle_export'::regclass)
"""
ROLE = "public.worknest_current_role()"
LIVE = "(state='active' OR (state='pending_delete' AND deletion_due_at>statement_timestamp()))"
ADMIN = f"org_id={ORG} AND {ROLE} IN ('Owner','Admin') AND public.worknest_org_active()"
BG = f"org_id={ORG} AND public.worknest_lifecycle_job()"
MAINT = f"""
org_id={ORG} AND public.worknest_lifecycle_job() AND EXISTS(SELECT 1 FROM
lifecycle_maintenance WHERE id={J} AND org_id={ORG})
"""

SQL = f"""
CREATE OR REPLACE FUNCTION public.worknest_org_active() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
SELECT EXISTS(SELECT 1 FROM organizations_organization WHERE id={ORG} AND {LIVE})
$fn$;
DROP POLICY org_visible ON organizations_organization;
CREATE POLICY org_visible ON organizations_organization FOR SELECT USING (
CASE WHEN {OWNER} THEN id={ORG} ELSE
({LIVE} AND ((id={ORG} AND {ROLE} IS NOT NULL) OR
(current_setting('worknest.discovery',true)='on' AND EXISTS(
 SELECT 1 FROM organizations_membership m JOIN accounts_user u ON u.id=m.user_id
 WHERE m.org_id=organizations_organization.id AND m.user_id={A} AND m.state='active' AND
u.is_active))))
OR (id={ORG} AND public.worknest_invited_role() IS NOT NULL)
OR (id={ORG} AND EXISTS(SELECT 1 FROM organizations_outboxevent e WHERE
 e.id=NULLIF(current_setting('worknest.email_event',true),'')::uuid AND e.org_id={ORG} AND
e.topic='invitation.email')) END);
DROP POLICY org_settings ON organizations_organization;
CREATE POLICY org_settings ON organizations_organization FOR UPDATE USING (
id={ORG} AND {LIVE} AND {ROLE} IN ('Owner','Admin')) WITH CHECK (
id={ORG} AND {LIVE} AND {ROLE} IN ('Owner','Admin'));

ALTER TABLE lifecycle_export ENABLE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_export FORCE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_maintenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_maintenance FORCE ROW LEVEL SECURITY;
CREATE POLICY export_read ON lifecycle_export FOR SELECT USING (
CASE WHEN {OWNER} THEN true ELSE {ADMIN} END);
CREATE POLICY export_create ON lifecycle_export FOR INSERT WITH CHECK ({ADMIN} AND requester_id IN (
SELECT id FROM organizations_membership WHERE org_id={ORG} AND user_id={A} AND state='active'));
CREATE POLICY export_update ON lifecycle_export FOR UPDATE USING ({ADMIN}) WITH CHECK ({ADMIN});
CREATE POLICY maintenance_read ON lifecycle_maintenance FOR SELECT USING (
CASE WHEN {OWNER} THEN true ELSE {ADMIN} END);
CREATE POLICY maintenance_create ON lifecycle_maintenance FOR INSERT WITH CHECK ({ADMIN});
CREATE POLICY maintenance_update ON lifecycle_maintenance FOR UPDATE USING ({ADMIN}) WITH
CHECK ({ADMIN});
ALTER TABLE lifecycle_export ADD CONSTRAINT export_requester_same_org FOREIGN
KEY(org_id,requester_id)
REFERENCES organizations_membership(org_id,id) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION public.worknest_lifecycle_job() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
SELECT {A} IS NULL AND (EXISTS(SELECT 1 FROM lifecycle_export WHERE org_id={ORG} AND id={J})
OR EXISTS(SELECT 1 FROM lifecycle_maintenance WHERE org_id={ORG} AND id={J}))
$fn$;
CREATE FUNCTION public.worknest_pending_lifecycle_jobs()
RETURNS TABLE(org_id uuid,job_id uuid,kind text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
SELECT org_id,id,'export'::text FROM lifecycle_export WHERE state IN ('queued','running')
AND next_attempt_at<=statement_timestamp()
UNION ALL SELECT org_id,id,'maintenance'::text FROM lifecycle_maintenance WHERE state IN
('queued','running')
AND next_attempt_at<=statement_timestamp() LIMIT 200
$fn$;
CREATE POLICY lifecycle_org_read ON organizations_organization FOR SELECT USING (
CASE WHEN {OWNER} THEN false ELSE id={ORG} AND public.worknest_lifecycle_job() END);
CREATE POLICY lifecycle_org_delete ON organizations_organization FOR DELETE USING (
id={ORG} AND public.worknest_lifecycle_job() AND state='pending_delete'
AND deletion_due_at<=statement_timestamp()-interval '10 minutes');
"""
TABLES = [
    "organizations_membership",
    "organizations_organizationusage",
    "organizations_auditevent",
    "organizations_outboxevent",
    "organizations_invitation",
    "organizations_teamrequest",
    "projects_project",
    "projects_projectmembership",
    "projects_task",
    "projects_writerequest",
    "collab_comment",
    "collab_attachment",
    "activity_notification",
    "activity_preference",
    "activity_reconciliation",
    "lifecycle_export",
    "lifecycle_maintenance",
]
for table in TABLES:
    SQL += f"""
CREATE POLICY lifecycle_read ON {table} FOR SELECT USING (
CASE WHEN {OWNER} THEN false ELSE {BG} END);
CREATE POLICY lifecycle_update ON {table} FOR UPDATE USING ({BG}) WITH CHECK ({BG});
"""
    if table != "lifecycle_maintenance":
        SQL += f"CREATE POLICY lifecycle_delete ON {table} FOR DELETE USING ({MAINT});\n"
# Audit remains append-only for HTTP actors; workers only append normal lifecycle events.
SQL += "DROP POLICY lifecycle_update ON organizations_auditevent;\n"
for table in ["organizations_auditevent", "organizations_outboxevent"]:
    SQL += f"CREATE POLICY lifecycle_append ON {table} FOR INSERT WITH CHECK ({BG});\n"
# Creation retries contain org metadata but no org FK; remove only matching expired/purged entries.
SQL += f"""
CREATE POLICY lifecycle_creation_read ON organizations_creationrequest FOR SELECT USING (
{A} IS NULL AND public.worknest_lifecycle_job() AND response->'organization'->>'id'={ORG}::text);
CREATE POLICY lifecycle_creation_delete ON organizations_creationrequest FOR DELETE USING (
{A} IS NULL AND public.worknest_lifecycle_job() AND response->'organization'->>'id'={ORG}::text);
ALTER TABLE organizations_organization NO FORCE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_maintenance NO FORCE ROW LEVEL SECURITY;
INSERT INTO lifecycle_maintenance(id,org_id,state,attempts,next_attempt_at,last_error)
SELECT gen_random_uuid(),id,'queued',0,statement_timestamp(),'' FROM organizations_organization;
ALTER TABLE organizations_organization FORCE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_maintenance FORCE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("lifecycle", "0001_initial"),
        ("organizations", "0008_organization_deletion_due_at"),
        ("activity", "0002_security"),
    ]
    operations = [migrations.RunSQL(SQL)]
