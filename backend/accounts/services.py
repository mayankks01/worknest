import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import AccountToken


def secret_for(token, user):
    # Derivable only with the server secret; password change invalidates queued links.
    return salted_hmac(
        "worknest-account-token-v1",
        f"{token.id}:{token.purpose}:{user.password}",
        algorithm="sha256",
    ).hexdigest()


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_token(user, purpose):
    """Caller holds a user row lock (or owns a newly inserted user)."""
    now = timezone.now()
    AccountToken.objects.filter(user=user, purpose=purpose, used_at=None).update(used_at=now)
    timeout = (
        settings.PASSWORD_RESET_TIMEOUT if purpose == "reset" else settings.ACCOUNT_VERIFY_TIMEOUT
    )
    token = AccountToken(user=user, purpose=purpose, expires_at=now + timedelta(seconds=timeout))
    token.token_hash = digest(secret_for(token, user))
    token.save()
    transaction.on_commit(lambda: schedule_email(str(token.id)))
    return token


def schedule_email(token_id):
    from .tasks import send_account_email

    try:
        send_account_email.apply_async(args=[token_id], retry=False)
    except Exception:
        # A periodic database scan recovers broker publication failures. Do not log
        # exception strings because provider/connection errors can contain secrets.
        pass


def valid_token(token, user, secret, purpose):
    return (
        token.purpose == purpose
        and user.is_active
        and token.used_at is None
        and timezone.now() < token.expires_at
        and constant_time_compare(token.token_hash, digest(secret))
        and constant_time_compare(token.token_hash, digest(secret_for(token, user)))
    )
