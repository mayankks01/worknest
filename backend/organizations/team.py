"""Team operations share a per-organization usage lock and live authorization."""

import hashlib
import json
import re
import uuid
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.throttle import check_rate
from accounts.views import InputError, email_input, endpoint, error

from .context import tenant_transaction
from .models import (
    AuditEvent,
    Invitation,
    Membership,
    Organization,
    OrganizationUsage,
    OutboxEvent,
    TeamRequest,
)
from .views import authenticated, unavailable

NON_OWNER = {"Admin", "Member", "Viewer"}


class TeamError(Exception):
    def __init__(self, code, message, status):
        self.code, self.message, self.status = code, message, status


def team_endpoint(methods, fields=()):
    def decorate(view):
        @endpoint(methods, fields)
        def wrapped(request, data, **kwargs):
            denied = authenticated(request)
            if denied is not None:
                return denied
            try:
                return view(request, data, **kwargs)
            except TeamError as exc:
                return error(request, exc.code, exc.message, exc.status)

        return wrapped

    return decorate


def actor_for(request, org_id, *, manage=False, lock=False):
    org = Organization.objects.filter(pk=org_id, state="active").first()
    actor = Membership.objects.filter(org_id=org_id, user=request.user, state="active").first()
    if not org or not actor:
        raise TeamError("NOT_FOUND", "This organization is unavailable.", 404)
    if manage and actor.role not in {"Owner", "Admin"}:
        raise TeamError("FORBIDDEN", "Only an Owner or Admin can manage the team.", 403)
    if lock:
        # All membership, invitation and ownership writes share this ordering.
        OrganizationUsage.objects.select_for_update().get(org_id=org_id)
        return actor_for(request, org_id, manage=manage)
    return org, actor


def can_manage(actor, role):
    if actor.role != "Owner" and role not in {"Member", "Viewer"}:
        raise TeamError("FORBIDDEN", "Only the Owner can manage Admin access.", 403)


def role_input(data):
    role = data.get("role", "")
    if role not in NON_OWNER:
        raise InputError({"role": ["Choose Admin, Member, or Viewer."]})
    return role


def throttle(request, scope, identity):
    wait = check_rate(request, scope, identity)
    if wait:
        response = error(request, "RATE_LIMITED", "Too many attempts. Try again later.", 429)
        response["Retry-After"] = str(wait)
        return response
    return None


def event(org, actor, action, resource_id, summary, *, email=False, payload=None):
    audit = AuditEvent.objects.create(
        org=org,
        actor_membership_id=actor.id,
        action=action,
        resource_id=resource_id,
        summary=summary,
    )
    outbox = OutboxEvent.objects.create(
        org=org,
        audit_id=audit.id,
        topic="invitation.email" if email else action,
        payload=payload or {"audit_id": str(audit.id), "resource_id": str(resource_id)},
    )
    if email:
        transaction.on_commit(lambda: publish_email(str(org.id), str(outbox.id)))
    elif payload and payload.get("kind") in {"assignment", "comment"}:
        from activity.tasks import publish

        transaction.on_commit(lambda: publish("event", org.id, outbox.id))
    return outbox


def publish_email(org_id, event_id):
    from .tasks import send_invitation

    try:
        send_invitation.apply_async(args=[org_id, event_id], retry=False)
    except Exception:
        # The committed outbox is recovered by beat, including broker outages.
        pass


def invitation_secret(invitation):
    return salted_hmac(
        "worknest-invitation-v1",
        f"{invitation.org_id}:{invitation.id}:{invitation.email}:{invitation.token_version}",
        algorithm="sha256",
    ).hexdigest()


def token_digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def queue_invitation(org, actor, invitation, action):
    invitation.token_hash = token_digest(invitation_secret(invitation))
    invitation.expires_at = timezone.now() + timedelta(hours=72)
    outbox = event(
        org,
        actor,
        action,
        invitation.id,
        {"role": invitation.role},
        email=True,
        payload={"invitation_id": str(invitation.id), "version": invitation.token_version},
    )
    invitation.email_event_id = outbox.id
    invitation.save()


def member_data(member):
    return {
        "id": str(member.id),
        "user_id": str(member.user_id),
        "display_name": member.user.display_name,
        "email": member.user.email,
        "role": member.role,
        "state": member.state,
    }


def invitation_data(invitation):
    delivery = OutboxEvent.objects.filter(pk=invitation.email_event_id).first()
    state = (
        "expired"
        if invitation.state == "pending" and invitation.expires_at <= timezone.now()
        else invitation.state
    )
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "role": invitation.role,
        "state": state,
        "expires_at": invitation.expires_at.isoformat(),
        "delivery_state": delivery.delivery_state if delivery else "queued",
        "attempts": delivery.attempts if delivery else 0,
    }


def page(request, queryset, label, serializer):
    salt = f"team:{request.user.id}:{label}"
    try:
        limit = int(request.GET.get("limit", 25))
        if not 1 <= limit <= 100:
            raise ValueError
        cursor = request.GET.get("cursor")
        if cursor:
            after = uuid.UUID(signing.loads(cursor, salt=salt, max_age=86400))
            queryset = queryset.filter(id__gt=after)
    except (ValueError, signing.BadSignature):
        raise InputError({"cursor": ["Invalid page. Refresh the list."]}) from None
    rows = list(queryset.order_by("id")[: limit + 1])
    return {
        "results": [serializer(row) for row in rows[:limit]],
        "next_cursor": signing.dumps(str(rows[limit - 1].id), salt=salt)
        if len(rows) > limit
        else None,
    }


@team_endpoint("GET")
def members(request, data, org_id):
    with tenant_transaction(request.user.id, org_id):
        _, actor = actor_for(request, org_id)
        result = page(
            request,
            Membership.objects.filter(org_id=org_id, state="active").select_related("user"),
            f"{org_id}:members",
            member_data,
        )
        usage = OrganizationUsage.objects.get(org_id=org_id)
        result["usage"] = {
            "active_members": usage.active_members,
            "member_limit": usage.member_limit,
        }
        result["role"] = actor.role
        return JsonResponse(result)


@team_endpoint("PATCH, DELETE", ("role",))
def member_detail(request, data, org_id, member_id):
    with tenant_transaction(request.user.id, org_id):
        org, actor = actor_for(request, org_id, manage=True, lock=True)
        member = (
            Membership.objects.filter(pk=member_id, org_id=org_id, state="active")
            .select_related("user")
            .first()
        )
        if not member:
            return unavailable(request)
        if member.role == "Owner":
            raise TeamError(
                "OWNER_PROTECTED", "Use ownership transfer before changing the Owner.", 403
            )
        can_manage(actor, member.role)
        if request.method == "PATCH":
            role = role_input(data)
            can_manage(actor, role)
            if role != member.role:
                if role == "Viewer":
                    from projects.services import clear_assignments

                    clear_assignments(org, actor, member, remove_access=False)
                previous = member.role
                member.role = role
                member.save(update_fields=["role"])
                event(
                    org,
                    actor,
                    "membership.role_changed",
                    member.id,
                    {"before": previous, "after": role},
                )
            return JsonResponse({"member": member_data(member)})
        event(org, actor, "membership.removed", member.id, {"role": member.role})
        from projects.services import clear_assignments

        clear_assignments(org, actor, member)
        member.state = "removed"
        member.save(update_fields=["state"])
        usage = OrganizationUsage.objects.get(org_id=org_id)
        usage.active_members -= 1
        usage.save(update_fields=["active_members"])
        return JsonResponse({"message": "Member removed. Their access has ended."})


@team_endpoint("GET, POST", ("email", "role"))
def invitations(request, data, org_id):
    with tenant_transaction(request.user.id, org_id):
        org, actor = actor_for(request, org_id, manage=True, lock=request.method == "POST")
        if request.method == "GET":
            return JsonResponse(
                page(
                    request,
                    Invitation.objects.filter(org_id=org_id),
                    f"{org_id}:invitations",
                    invitation_data,
                )
            )
        email, role = email_input(data), role_input(data)
        can_manage(actor, role)
        limited = throttle(request, "invitation", f"{org_id}:{request.user.id}")
        if limited is not None:
            return limited
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise InputError(
                {"idempotency_key": ["Send a unique Idempotency-Key of 16–128 characters."]}
            )
        digest = token_digest(json.dumps({"email": email, "role": role}, sort_keys=True))
        saved = TeamRequest.objects.filter(org_id=org_id, actor=request.user, key=key).first()
        if saved and saved.expires_at > timezone.now():
            if saved.payload_hash != digest:
                raise TeamError(
                    "IDEMPOTENCY_CONFLICT", "This request key was used for another invitation.", 409
                )
            return JsonResponse(saved.response, status=201)
        if saved:
            saved.delete()
        Invitation.objects.filter(
            org_id=org_id, email=email, state="pending", expires_at__lte=timezone.now()
        ).update(state="expired")
        if Invitation.objects.filter(org_id=org_id, email=email, state="pending").exists():
            raise TeamError(
                "INVITATION_EXISTS",
                "A pending invitation already exists. Use its resend action.",
                409,
            )
        invitation = Invitation(org=org, email=email, role=role, created_by_membership_id=actor.id)
        queue_invitation(org, actor, invitation, "invitation.created")
        result = {"invitation": invitation_data(invitation)}
        TeamRequest.objects.create(
            org=org,
            actor=request.user,
            key=key,
            payload_hash=digest,
            response=result,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        return JsonResponse(result, status=201)


@team_endpoint("POST")
def invitation_action(request, data, org_id, invitation_id, action):
    with tenant_transaction(request.user.id, org_id):
        org, actor = actor_for(request, org_id, manage=True, lock=True)
        invitation = Invitation.objects.filter(org_id=org_id, pk=invitation_id).first()
        if not invitation:
            return unavailable(request)
        can_manage(actor, invitation.role)
        if invitation.state not in {"pending", "expired"}:
            raise TeamError("INVITATION_CLOSED", "This invitation is already closed.", 409)
        limited = throttle(request, "invitation", f"{org_id}:{request.user.id}")
        if limited is not None:
            return limited
        if action == "revoke":
            invitation.state = "revoked"
            invitation.save(update_fields=["state"])
            event(org, actor, "invitation.revoked", invitation.id, {})
        else:
            if (
                Invitation.objects.filter(org_id=org_id, email=invitation.email, state="pending")
                .exclude(pk=invitation.id)
                .exists()
            ):
                raise TeamError(
                    "INVITATION_EXISTS", "A newer invitation exists for this address.", 409
                )
            invitation.state = "pending"
            invitation.token_version += 1
            queue_invitation(org, actor, invitation, "invitation.resent")
        return JsonResponse({"invitation": invitation_data(invitation)})


def parse_invitation(data):
    try:
        org_id, invitation_id, secret = data.get("token", "").split(".")
        org_id, invitation_id = uuid.UUID(org_id), uuid.UUID(invitation_id)
        if not re.fullmatch(r"[a-f0-9]{64}", secret):
            raise ValueError
    except ValueError:
        raise TeamError(
            "INVALID_INVITATION",
            "This invitation is invalid, expired, or unavailable for this account.",
            400,
        ) from None
    return org_id, invitation_id, secret


@team_endpoint("POST", ("token",))
def preview(request, data):
    limited = throttle(request, "invitation", str(request.user.id))
    if limited is not None:
        return limited
    org_id, invitation_id, secret = parse_invitation(data)
    with tenant_transaction(request.user.id, org_id, invitation_hash=token_digest(secret)):
        invitation = Invitation.objects.filter(
            pk=invitation_id, org_id=org_id, state="pending"
        ).first()
        if (
            not invitation
            or invitation.expires_at <= timezone.now()
            or invitation.email != request.user.email
            or not request.user.verified_at
            or not constant_time_compare(invitation.token_hash, token_digest(secret))
            or not constant_time_compare(invitation_secret(invitation), secret)
        ):
            raise TeamError(
                "INVALID_INVITATION",
                "This invitation is invalid, expired, or unavailable for this account.",
                400,
            )
        org = Organization.objects.filter(pk=org_id, state="active").first()
        if not org:
            raise TeamError("INVALID_INVITATION", "This invitation is unavailable.", 400)
        return JsonResponse(
            {
                "organization_name": org.display_name,
                "role": invitation.role,
                "expires_at": invitation.expires_at.isoformat(),
            }
        )


@team_endpoint("POST", ("token",))
def accept(request, data):
    if not request.user.verified_at:
        raise TeamError(
            "EMAIL_NOT_VERIFIED", "Verify your email before accepting an invitation.", 403
        )
    limited = throttle(request, "invitation", str(request.user.id))
    if limited is not None:
        return limited
    org_id, invitation_id, secret = parse_invitation(data)
    with tenant_transaction(request.user.id, org_id, invitation_hash=token_digest(secret)):
        # Usage precedes invitation/member locks in every team mutation.
        usage = OrganizationUsage.objects.select_for_update().filter(org_id=org_id).first()
        invitation = (
            Invitation.objects.select_for_update()
            .filter(org_id=org_id, pk=invitation_id, state="pending")
            .first()
        )
        if (
            not usage
            or not invitation
            or invitation.expires_at <= timezone.now()
            or invitation.email != request.user.email
            or not constant_time_compare(invitation.token_hash, token_digest(secret))
            or not constant_time_compare(invitation_secret(invitation), secret)
        ):
            raise TeamError(
                "INVALID_INVITATION",
                "This invitation is invalid, expired, or unavailable for this account.",
                400,
            )
        member = Membership.objects.filter(org_id=org_id, user=request.user).first()
        if not member or member.state != "active":
            if usage.active_members >= usage.member_limit:
                raise TeamError(
                    "MEMBER_LIMIT",
                    "This organization has reached its member limit. "
                    "Ask its Owner to free a place.",
                    409,
                )
            if member:
                member.state, member.role = "active", invitation.role
                member.save(update_fields=["state", "role"])
            else:
                member = Membership.objects.create(
                    org_id=org_id, user=request.user, role=invitation.role
                )
            usage.active_members += 1
            usage.save(update_fields=["active_members"])
        org = Organization.objects.get(pk=org_id, state="active")
        # Audit before consumption so matching-email capability is still valid.
        event(org, member, "invitation.accepted", invitation.id, {"role": member.role})
        invitation.state, invitation.accepted_at = "accepted", timezone.now()
        invitation.save(update_fields=["state", "accepted_at"])
        return JsonResponse(
            {"organization_id": str(org_id), "message": "You’re in. Welcome to your organization."}
        )


@team_endpoint("POST", ("member_id", "password"))
def transfer(request, data, org_id):
    limited = throttle(request, "reauth", request.user.email)
    if limited is not None:
        return limited
    with tenant_transaction(request.user.id, org_id):
        org, actor = actor_for(request, org_id, manage=True, lock=True)
        if actor.role != "Owner":
            raise TeamError("FORBIDDEN", "Only the current Owner can transfer ownership.", 403)
        password = data.get("password", "")
        if len(password) > 128 or not request.user.check_password(password):
            raise TeamError("REAUTH_REQUIRED", "Your current password is incorrect.", 403)
        try:
            member_id = uuid.UUID(data.get("member_id", ""))
        except ValueError:
            raise InputError({"member_id": ["Choose an active member."]}) from None
        target = Membership.objects.filter(
            org_id=org_id, pk=member_id, state="active", user__is_active=True
        ).first()
        if not target or target.id == actor.id:
            raise InputError({"member_id": ["Choose another active member."]})
        org = Organization.objects.select_for_update().get(pk=org_id)
        list(
            Membership.objects.select_for_update()
            .filter(pk__in=[actor.id, target.id])
            .order_by("id")
        )
        event(
            org,
            actor,
            "ownership.transferred",
            target.id,
            {"previous_owner": str(actor.id), "new_owner": str(target.id)},
        )
        org.owner_membership_id = target.id
        org.save(update_fields=["owner_membership_id"])
        actor.role = "Admin"
        actor.save(update_fields=["role"])
        target.role = "Owner"
        target.save(update_fields=["role"])
        return JsonResponse({"message": "Ownership transferred. Your role is now Admin."})
