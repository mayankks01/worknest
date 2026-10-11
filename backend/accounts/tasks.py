import secrets
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import AccountToken
from .services import secret_for, valid_token


@shared_task(name="accounts.dispatch_emails")
def dispatch_emails():
    now = timezone.now()
    AccountToken.objects.filter(
        delivery_state="sending", attempts__gte=5, next_attempt_at__lte=now
    ).update(delivery_state="failed")
    ids = AccountToken.objects.filter(
        used_at=None,
        expires_at__gt=now,
        attempts__lt=5,
        delivery_state__in=["queued", "sending"],
        next_attempt_at__lte=now,
    ).values_list("id", flat=True)[:100]
    for token_id in ids:
        send_account_email.apply_async(args=[str(token_id)], retry=False)


@shared_task(name="accounts.send_email", ignore_result=True)
def send_account_email(token_id):
    with transaction.atomic():
        token = AccountToken.objects.select_for_update().filter(pk=token_id).first()
        now = timezone.now()
        if not token or token.delivery_state not in ("queued", "sending"):
            return
        if token.next_attempt_at > now or token.attempts >= 5:
            return
        user = token.user
        secret = secret_for(token, user)
        if not valid_token(token, user, secret, token.purpose):
            token.delivery_state = "obsolete"
            token.save(update_fields=["delivery_state"])
            return
        token.attempts += 1
        attempt = token.attempts
        token.delivery_state = "sending"
        token.next_attempt_at = now + timedelta(minutes=2)
        token.save(update_fields=["attempts", "delivery_state", "next_attempt_at"])
    # No SMTP call while holding a transaction. URL fragment avoids access logs
    # and Referer leakage; the client exchanges it in a CSRF-protected POST.
    route = "reset-password" if token.purpose == "reset" else "verify-email"
    link = f"{settings.PUBLIC_APP_URL}/{route}#{token.id}.{secret}"
    action = "Reset your password" if token.purpose == "reset" else "Verify your email"
    lifetime = "30 minutes" if token.purpose == "reset" else "24 hours"
    try:
        sent = send_mail(
            f"{action} — WorkNest",
            f"{action}\n\n{link}\n\nThis link is single-use and expires in {lifetime}.\n"
            "If you did not request this, you can ignore this email.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        if sent != 1:
            raise RuntimeError("Email not accepted")
    except Exception:
        AccountToken.objects.filter(pk=token_id, attempts=attempt).update(
            delivery_state="failed" if attempt >= 5 else "queued",
            next_attempt_at=timezone.now()
            + timedelta(seconds=30 * 2**attempt + secrets.randbelow(15)),
        )
    else:
        AccountToken.objects.filter(pk=token_id, attempts=attempt).update(
            delivery_state="sent", sent_at=timezone.now()
        )
