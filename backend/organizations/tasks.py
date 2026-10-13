import secrets
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import connection
from django.utils import timezone

from .context import tenant_transaction
from .models import Invitation, Organization, OutboxEvent
from .team import invitation_secret


@shared_task(name="organizations.dispatch_invitations")
def dispatch_invitations():
    # Narrow SECURITY DEFINER function returns routing IDs only. Every subsequent
    # read/write uses a transaction scoped to that one org and invitation event.
    with connection.cursor() as cursor:
        cursor.execute("SELECT org_id, event_id FROM public.worknest_pending_invitation_jobs()")
        routes = cursor.fetchall()
    for org_id, event_id in routes:
        try:
            send_invitation.apply_async(args=[str(org_id), str(event_id)], retry=False)
        except Exception:
            break  # Next beat tick retries; committed records remain queued.


@shared_task(name="organizations.send_invitation", ignore_result=True)
def send_invitation(org_id, event_id):
    with tenant_transaction(None, org_id, email_event=event_id):
        job = (
            OutboxEvent.objects.select_for_update()
            .filter(pk=event_id, org_id=org_id, topic="invitation.email")
            .first()
        )
        now = timezone.now()
        if not job or job.delivery_state not in {"queued", "sending"} or job.next_attempt_at > now:
            return
        if job.attempts >= 5:
            job.delivery_state = "failed"
            job.save(update_fields=["delivery_state"])
            return
        invitation = Invitation.objects.filter(
            pk=job.payload.get("invitation_id"), org_id=org_id, email_event_id=event_id
        ).first()
        org = Organization.objects.filter(Organization.live_filter(), pk=org_id).first()
        if (
            not invitation
            or not org
            or invitation.state != "pending"
            or invitation.expires_at <= now
            or invitation.token_version != job.payload.get("version")
        ):
            job.delivery_state = "obsolete"
            job.save(update_fields=["delivery_state"])
            return
        job.attempts += 1
        attempt = job.attempts
        job.delivery_state = "sending"
        job.next_attempt_at = now + timedelta(minutes=2)
        job.save(update_fields=["attempts", "delivery_state", "next_attempt_at"])
        link = (
            f"{settings.PUBLIC_APP_URL}/invitations/accept#"
            f"{org_id}.{invitation.id}.{invitation_secret(invitation)}"
        )
        recipient, org_name, role = invitation.email, org.display_name, invitation.role
    # SMTP never holds the usage/business transaction or the job claim lock.
    try:
        sent = send_mail(
            "You’re invited to WorkNest",
            f"You’re invited to join {org_name} as {role}.\n\n{link}\n\n"
            "Sign in with this email address and verify it before accepting. "
            "This link is single-use and expires in 72 hours. "
            "If you weren’t expecting this invitation, you can ignore it.",
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=False,
        )
        if sent != 1:
            raise RuntimeError("Email not accepted")
    except Exception:
        with tenant_transaction(None, org_id, email_event=event_id):
            OutboxEvent.objects.filter(
                pk=event_id, attempts=attempt, delivery_state="sending"
            ).update(
                delivery_state="failed" if attempt >= 5 else "queued",
                next_attempt_at=timezone.now()
                + timedelta(seconds=30 * 2**attempt + secrets.randbelow(15)),
            )
    else:
        with tenant_transaction(None, org_id, email_event=event_id):
            OutboxEvent.objects.filter(
                pk=event_id, attempts=attempt, delivery_state="sending"
            ).update(delivery_state="sent", dispatched_at=timezone.now())
