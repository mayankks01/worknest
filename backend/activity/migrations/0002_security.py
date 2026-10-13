from django.db import migrations

ORG = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
ACTOR = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
JOB = "NULLIF(current_setting('worknest.activity_id', true), '')::uuid"
ROLE = "public.worknest_current_role()"
OWNER = """
current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE
oid='public.activity_notification'::regclass)
"""
SCOPE = f"org_id={ORG} AND public.worknest_org_active() AND {ROLE} IS NOT NULL"
ADMIN = f"org_id={ORG} AND {ROLE} IN ('Owner','Admin')"
SELF = f"""
{SCOPE} AND recipient_id IN (SELECT id FROM organizations_membership WHERE org_id={ORG} AND
user_id={ACTOR} AND state='active')
"""
PREF = SELF.replace("recipient_id", "member_id")
BG = f"org_id={ORG} AND public.worknest_activity_job()"

SQL = f"""
-- Owner-only catalog/routing access supports fixed-search-path worker helpers.
DROP POLICY outbox_read ON organizations_outboxevent;
CREATE POLICY outbox_read ON organizations_outboxevent FOR SELECT USING (
 CASE WHEN {OWNER} THEN true ELSE org_id={ORG} AND ({ROLE} IS NOT NULL
 OR (id=NULLIF(current_setting('worknest.email_event',true),'')::uuid AND
topic='invitation.email')) END
);
ALTER TABLE activity_notification ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_notification FORCE ROW LEVEL SECURITY;
ALTER TABLE activity_preference ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_preference FORCE ROW LEVEL SECURITY;
ALTER TABLE activity_reconciliation ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_reconciliation FORCE ROW LEVEL SECURITY;
CREATE POLICY notification_read ON activity_notification FOR SELECT USING (
 CASE WHEN {OWNER} THEN true ELSE ({SELF}) OR ({ADMIN}) END
);
CREATE POLICY notification_self_update ON activity_notification FOR UPDATE USING ({SELF}) WITH
CHECK ({SELF});
CREATE POLICY preference_self ON activity_preference FOR ALL USING ({PREF}) WITH CHECK ({PREF});
CREATE POLICY reconciliation_read ON activity_reconciliation FOR SELECT USING (
 CASE WHEN {OWNER} THEN true ELSE {ADMIN} END
);
CREATE POLICY reconciliation_create ON activity_reconciliation FOR INSERT WITH CHECK ({ADMIN});
CREATE POLICY reconciliation_manage ON activity_reconciliation FOR UPDATE USING ({ADMIN}) WITH
CHECK ({ADMIN});

CREATE FUNCTION public.worknest_activity_job() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $fn$
 SELECT {ACTOR} IS NULL AND (
 EXISTS(SELECT 1 FROM public.organizations_outboxevent WHERE org_id={ORG} AND id={JOB} AND
topic<>'invitation.email')
 OR EXISTS(SELECT 1 FROM public.activity_notification WHERE org_id={ORG} AND id={JOB})
 OR EXISTS(SELECT 1 FROM public.activity_reconciliation WHERE org_id={ORG} AND id={JOB}))
$fn$;
CREATE FUNCTION public.worknest_pending_activity_jobs()
RETURNS TABLE(org_id uuid, job_id uuid, kind text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $fn$
 SELECT org_id,id,'event'::text FROM public.organizations_outboxevent
 WHERE topic<>'invitation.email' AND delivery_state IN ('queued','processing') AND
next_attempt_at<=statement_timestamp()
 UNION ALL
 SELECT org_id,id,'email'::text FROM public.activity_notification
 WHERE email_state IN ('queued','sending') AND next_attempt_at<=statement_timestamp()
 UNION ALL
 SELECT org_id,id,'reconcile'::text FROM public.activity_reconciliation
 WHERE state IN ('queued','running') AND next_attempt_at<=statement_timestamp()
 LIMIT 300
$fn$;
CREATE POLICY activity_org_read ON organizations_organization FOR SELECT USING (
 CASE WHEN {OWNER} THEN false ELSE id={ORG} AND public.worknest_activity_job() END
);
DROP POLICY audit_read ON organizations_auditevent;
CREATE POLICY audit_read ON organizations_auditevent FOR SELECT USING ({SCOPE});
CREATE POLICY activity_audit_append ON organizations_auditevent FOR INSERT WITH CHECK ({BG});
CREATE POLICY activity_outbox_append ON organizations_outboxevent FOR INSERT WITH CHECK ({BG});
CREATE POLICY outbox_admin_retry ON organizations_outboxevent FOR UPDATE USING ({ADMIN}) WITH
CHECK ({ADMIN});
CREATE POLICY notification_admin_retry ON activity_notification FOR UPDATE USING ({ADMIN}) WITH
CHECK ({ADMIN});
"""
for table in [
    "organizations_membership",
    "organizations_organizationusage",
    "organizations_auditevent",
    "organizations_outboxevent",
    "projects_project",
    "projects_projectmembership",
    "projects_task",
    "collab_attachment",
    "collab_comment",
    "activity_notification",
    "activity_preference",
    "activity_reconciliation",
]:
    SQL += f"""
CREATE POLICY activity_job_read ON {table} FOR SELECT USING (CASE WHEN {OWNER} THEN false ELSE
{BG} END);
    """
for table in [
    "organizations_outboxevent",
    "organizations_organizationusage",
    "activity_notification",
    "activity_reconciliation",
    "collab_attachment",
]:
    SQL += (
        f"CREATE POLICY activity_job_update ON {table} FOR UPDATE USING ({BG}) WITH CHECK ({BG});\n"
    )
SQL += (
    f"CREATE POLICY activity_inbox_insert ON activity_notification FOR INSERT WITH CHECK ({BG});\n"
)
for table, fields in {
    "activity_notification": [
        ("recipient_id", "organizations_membership"),
        ("task_id", "projects_task"),
        ("source_id", "organizations_outboxevent"),
    ],
    "activity_preference": [("member_id", "organizations_membership")],
}.items():
    for field, target in fields:
        SQL += f"""
ALTER TABLE {table} ADD CONSTRAINT {table}_{field}_tenant FOREIGN KEY (org_id,{field})
REFERENCES {target}(org_id,id) DEFERRABLE INITIALLY DEFERRED;
        """
# Seed one durable reconciliation schedule per existing organization, within this migration.
SQL += """
ALTER TABLE organizations_organization NO FORCE ROW LEVEL SECURITY;
ALTER TABLE activity_reconciliation NO FORCE ROW LEVEL SECURITY;
INSERT INTO
activity_reconciliation(id,org_id,state,attempts,next_attempt_at,last_error,corrected_counters,object_cursor,orphan_count)
SELECT gen_random_uuid(),id,'queued',0,statement_timestamp(),'','[]'::jsonb,'',0 FROM
organizations_organization;
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;
ALTER TABLE organizations_organization FORCE ROW LEVEL SECURITY;
ALTER TABLE activity_reconciliation FORCE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("activity", "0001_initial"), ("collab", "0002_tenant_security")]
    operations = [migrations.RunSQL(SQL)]
