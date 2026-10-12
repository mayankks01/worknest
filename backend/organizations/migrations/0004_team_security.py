from django.db import migrations

A = "NULLIF(current_setting('worknest.actor_id', true), '')::uuid"
ORG = "NULLIF(current_setting('worknest.org_id', true), '')::uuid"
E = "NULLIF(current_setting('worknest.email_event', true), '')::uuid"
OWNER = """current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class
    WHERE oid = 'public.organizations_membership'::regclass)"""
ROLE = "public.worknest_current_role()"
INVITED = "public.worknest_invited_role()"

SQL = f"""
-- The migration is atomic. Temporarily exempt only the table owner while
-- backfilling existing organizations; restore FORCE before committing.
ALTER TABLE organizations_organization NO FORCE ROW LEVEL SECURITY;
ALTER TABLE organizations_membership NO FORCE ROW LEVEL SECURITY;
INSERT INTO organizations_organizationusage (org_id, active_members, member_limit)
SELECT o.id, count(m.id), GREATEST(30, count(m.id))
FROM organizations_organization o JOIN organizations_membership m
ON m.org_id=o.id AND m.state='active' GROUP BY o.id;
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;
ALTER TABLE organizations_organization FORCE ROW LEVEL SECURITY;
ALTER TABLE organizations_membership FORCE ROW LEVEL SECURITY;

CREATE FUNCTION public.worknest_current_role() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
    SELECT m.role FROM public.organizations_membership m
    JOIN public.accounts_user u ON u.id=m.user_id
    WHERE m.org_id={ORG} AND m.user_id={A} AND m.state='active' AND u.is_active
$fn$;
CREATE FUNCTION public.worknest_org_active() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
    SELECT EXISTS(SELECT 1 FROM public.organizations_organization WHERE id={ORG} AND state='active')
$fn$;
CREATE FUNCTION public.worknest_owner_membership() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
    SELECT owner_membership_id FROM public.organizations_organization WHERE id={ORG}
$fn$;
CREATE FUNCTION public.worknest_invited_role() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
    SELECT i.role FROM public.organizations_invitation i
    JOIN public.accounts_user u ON u.email=i.email AND u.id={A}
    WHERE i.org_id={ORG} AND i.state='pending' AND i.expires_at > statement_timestamp()
    AND i.token_hash=current_setting('worknest.invitation_hash', true)
    AND u.is_active AND u.verified_at IS NOT NULL AND public.worknest_org_active()
    LIMIT 1
$fn$;

DROP POLICY membership_self ON organizations_membership;
CREATE POLICY membership_read ON organizations_membership FOR SELECT USING (
    CASE WHEN {OWNER} THEN user_id={A} AND
        (org_id={ORG} OR current_setting('worknest.discovery', true)='on')
    ELSE
        (user_id={A} AND state='active' AND
            (org_id={ORG} OR current_setting('worknest.discovery', true)='on'))
        OR (org_id={ORG} AND {ROLE} IS NOT NULL)
        OR (org_id={ORG} AND user_id={A} AND {INVITED} IS NOT NULL)
    END
);
CREATE POLICY membership_join ON organizations_membership FOR INSERT WITH CHECK (
    org_id={ORG} AND user_id={A} AND state='active' AND role={INVITED}
);
CREATE POLICY membership_manage ON organizations_membership FOR UPDATE USING (
    org_id={ORG} AND (
        {ROLE}='Owner' OR ({ROLE}='Admin' AND role IN ('Member','Viewer'))
        OR ({ROLE}='Admin' AND role<>'Owner' AND id=public.worknest_owner_membership())
        OR (user_id={A} AND state='removed' AND {INVITED} IS NOT NULL)
    )
) WITH CHECK (
    org_id={ORG} AND (
        {ROLE}='Owner' OR ({ROLE}='Admin' AND role IN ('Member','Viewer'))
        OR ({ROLE}='Admin' AND role='Owner' AND state='active' AND id=public.worknest_owner_membership())
        OR (user_id={A} AND state='active' AND role={INVITED})
    )
);

DROP POLICY org_visible ON organizations_organization;
CREATE POLICY org_visible ON organizations_organization FOR SELECT USING (
    CASE WHEN {OWNER} THEN id={ORG}
    ELSE (state='active' AND (
        (id={ORG} AND {ROLE} IS NOT NULL) OR
        (current_setting('worknest.discovery', true)='on' AND EXISTS (
            SELECT 1 FROM organizations_membership m JOIN accounts_user u ON u.id=m.user_id
            WHERE m.org_id=organizations_organization.id AND m.user_id={A} AND m.state='active' AND u.is_active))
    )) OR (id={ORG} AND {INVITED} IS NOT NULL) OR (id={ORG} AND EXISTS (SELECT 1 FROM organizations_outboxevent e
        WHERE e.id={E} AND e.org_id=organizations_organization.id AND e.topic='invitation.email'))
    END
);

ALTER TABLE organizations_invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations_invitation FORCE ROW LEVEL SECURITY;
CREATE POLICY invitation_read ON organizations_invitation FOR SELECT USING (
    CASE WHEN {OWNER} THEN org_id={ORG}
    ELSE org_id={ORG} AND (
        {ROLE} IN ('Owner','Admin') OR email_event_id={E} OR (
            token_hash=current_setting('worknest.invitation_hash', true) AND
            EXISTS (SELECT 1 FROM accounts_user u WHERE u.id={A} AND u.email=organizations_invitation.email
                AND u.verified_at IS NOT NULL AND u.is_active)
        )
    ) END
);
CREATE POLICY invitation_create ON organizations_invitation FOR INSERT WITH CHECK (
    org_id={ORG} AND ({ROLE}='Owner' OR ({ROLE}='Admin' AND role IN ('Member','Viewer')))
);
CREATE POLICY invitation_update ON organizations_invitation FOR UPDATE USING (
    org_id={ORG} AND ({ROLE}='Owner' OR ({ROLE}='Admin' AND role IN ('Member','Viewer'))
        OR (state='pending' AND role={INVITED}))
) WITH CHECK (
    org_id={ORG} AND ({ROLE}='Owner' OR ({ROLE}='Admin' AND role IN ('Member','Viewer'))
        OR (state='accepted' AND token_hash=current_setting('worknest.invitation_hash', true)
            AND EXISTS (SELECT 1 FROM accounts_user u WHERE u.id={A} AND u.email=organizations_invitation.email
                AND u.verified_at IS NOT NULL AND u.is_active)))
);
ALTER TABLE organizations_invitation ADD CONSTRAINT invitation_creator_same_org_fk
    FOREIGN KEY (org_id, created_by_membership_id) REFERENCES organizations_membership(org_id,id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE organizations_invitation ADD CONSTRAINT invitation_email_same_org_fk
    FOREIGN KEY (org_id, email_event_id) REFERENCES organizations_outboxevent(org_id,id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE organizations_organizationusage ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations_organizationusage FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_read ON organizations_organizationusage FOR SELECT USING (
    org_id={ORG} AND ({ROLE} IS NOT NULL OR {INVITED} IS NOT NULL)
);
CREATE POLICY usage_create ON organizations_organizationusage FOR INSERT WITH CHECK (
    org_id={ORG} AND {ROLE}='Owner'
);
CREATE POLICY usage_update ON organizations_organizationusage FOR UPDATE USING (
    org_id={ORG} AND ({ROLE} IN ('Owner','Admin') OR {INVITED} IS NOT NULL)
) WITH CHECK (org_id={ORG});
ALTER TABLE organizations_teamrequest ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations_teamrequest FORCE ROW LEVEL SECURITY;
CREATE POLICY team_request_scope ON organizations_teamrequest USING (
    org_id={ORG} AND actor_id={A} AND {ROLE} IN ('Owner','Admin')
) WITH CHECK (org_id={ORG} AND actor_id={A} AND {ROLE} IN ('Owner','Admin'));

-- Worker policy permits exactly the named invitation event in the named org.
-- Discovery returns routing IDs only, not email addresses or token material.
DROP POLICY outbox_read ON organizations_outboxevent;
CREATE POLICY outbox_read ON organizations_outboxevent FOR SELECT USING (
    CASE WHEN {OWNER} THEN
        topic='invitation.email' OR org_id={ORG}
    ELSE (org_id={ORG} AND {ROLE} IS NOT NULL)
        OR (org_id={ORG} AND id={E} AND topic='invitation.email') END
);
CREATE POLICY outbox_delivery ON organizations_outboxevent FOR UPDATE USING (
    org_id={ORG} AND id={E} AND topic='invitation.email'
) WITH CHECK (org_id={ORG} AND id={E} AND topic='invitation.email');
CREATE FUNCTION public.worknest_pending_invitation_jobs()
RETURNS TABLE(org_id uuid, event_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, public
AS $fn$
    SELECT e.org_id, e.id FROM public.organizations_outboxevent e
    WHERE e.topic='invitation.email' AND e.delivery_state IN ('queued','sending')
    AND e.next_attempt_at <= statement_timestamp()
    ORDER BY e.next_attempt_at, e.id LIMIT 100
$fn$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0003_outboxevent_attempts_outboxevent_delivery_state_and_more")
    ]
    # Security evolution is forward-only; restore a DB backup for a schema rollback.
    operations = [migrations.RunSQL(SQL)]
