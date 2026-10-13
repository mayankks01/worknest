from django.db import migrations

ORG = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
ACTOR = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
FILE = "NULLIF(current_setting('worknest.file_id', true), '')::uuid"
ROLE = "public.worknest_current_role()"
SCOPE = f"org_id={ORG} AND {ROLE} IS NOT NULL AND public.worknest_org_active()"
WRITE = f"{SCOPE} AND {ROLE} IN ('Owner','Admin','Member')"
OWNER = """current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class
    WHERE oid='public.collab_attachment'::regclass)"""
JOB = f"org_id={ORG} AND public.worknest_file_task() IS NOT NULL"

SQL = f"""
ALTER TABLE collab_comment ENABLE ROW LEVEL SECURITY;
ALTER TABLE collab_comment FORCE ROW LEVEL SECURITY;
CREATE POLICY comment_read ON collab_comment FOR SELECT USING ({SCOPE});
CREATE POLICY comment_create ON collab_comment FOR INSERT WITH CHECK ({WRITE});
CREATE POLICY comment_update ON collab_comment FOR UPDATE USING ({WRITE}) WITH CHECK ({WRITE});
ALTER TABLE collab_attachment ENABLE ROW LEVEL SECURITY;
ALTER TABLE collab_attachment FORCE ROW LEVEL SECURITY;
CREATE POLICY attachment_read ON collab_attachment FOR SELECT USING (
    CASE WHEN {OWNER} THEN true ELSE ({SCOPE}) OR (org_id={ORG} AND id={FILE}) END
);
CREATE POLICY attachment_create ON collab_attachment FOR INSERT WITH CHECK ({WRITE});
CREATE POLICY attachment_update ON collab_attachment FOR UPDATE USING (
    ({WRITE}) OR (org_id={ORG} AND id={FILE})
) WITH CHECK (({WRITE}) OR (org_id={ORG} AND id={FILE}));

CREATE FUNCTION public.worknest_file_task() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $fn$
    SELECT task_id FROM public.collab_attachment WHERE org_id={ORG} AND id={FILE}
$fn$;
CREATE FUNCTION public.worknest_file_author() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $fn$
    SELECT author_id FROM public.collab_attachment WHERE org_id={ORG} AND id={FILE}
$fn$;
CREATE FUNCTION public.worknest_pending_file_jobs()
RETURNS TABLE(org_id uuid, file_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $fn$
    SELECT a.org_id, a.id FROM public.collab_attachment a
    WHERE a.next_attempt_at <= statement_timestamp()
    AND ((a.cleanup_pending AND a.cleanup_attempts < 5)
         OR a.state IN ('uploading','queued','scanning'))
    ORDER BY a.next_attempt_at, a.id LIMIT 100
$fn$;

-- Background capability exposes only the named file's task and author context.
CREATE POLICY file_job_org ON organizations_organization FOR SELECT USING (
    id={ORG} AND public.worknest_file_task() IS NOT NULL
);
CREATE POLICY file_job_member ON organizations_membership FOR SELECT USING (
    {JOB} AND id=public.worknest_file_author()
);
CREATE POLICY file_job_task ON projects_task FOR SELECT USING (
    org_id={ORG} AND id=public.worknest_file_task()
);
CREATE POLICY file_job_project ON projects_project FOR SELECT USING (
    {JOB} AND id IN (SELECT project_id FROM projects_task WHERE id=public.worknest_file_task())
);
CREATE POLICY file_job_project_member ON projects_projectmembership FOR SELECT USING (
    {JOB} AND membership_id=public.worknest_file_author()
    AND project_id IN (SELECT project_id FROM projects_task WHERE id=public.worknest_file_task())
);
CREATE POLICY file_job_usage_read ON organizations_organizationusage FOR SELECT USING ({JOB});
CREATE POLICY file_job_usage_update ON organizations_organizationusage FOR UPDATE
USING ({JOB}) WITH CHECK ({JOB});
CREATE POLICY file_job_audit_read ON organizations_auditevent FOR SELECT USING (
    {JOB} AND resource_id={FILE}
);
CREATE POLICY file_job_audit_create ON organizations_auditevent FOR INSERT WITH CHECK (
    {JOB} AND resource_id={FILE} AND actor_membership_id=public.worknest_file_author()
);
CREATE POLICY file_job_outbox_read ON organizations_outboxevent FOR SELECT USING (
    {JOB} AND payload->>'resource_id'={FILE}::text
);
CREATE POLICY file_job_outbox_create ON organizations_outboxevent FOR INSERT WITH CHECK (
    {JOB} AND payload->>'resource_id'={FILE}::text
);
"""
for table in ["collab_comment", "collab_attachment"]:
    for field, target in [("task_id", "projects_task"), ("author_id", "organizations_membership")]:
        SQL += f"""
ALTER TABLE {table} ADD CONSTRAINT {table}_{field}_same_org
FOREIGN KEY (org_id,{field}) REFERENCES {target}(org_id,id) DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):
    dependencies = [("collab", "0001_initial")]
    operations = [migrations.RunSQL(SQL)]
