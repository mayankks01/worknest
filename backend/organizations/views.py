import hashlib
import json
import re
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone

from accounts.models import User
from accounts.views import InputError, endpoint, error

from .context import tenant_transaction
from .models import (
    AuditEvent,
    CreationRequest,
    Membership,
    Organization,
    OrganizationUsage,
    OutboxEvent,
)

FIELDS = ("display_name", "slug", "timezone")


def inputs(data, partial=False):
    result = {}
    if not partial or "display_name" in data:
        name = data.get("display_name", "").strip()
        if not 1 <= len(name) <= 80:
            raise InputError({"display_name": ["Use between 1 and 80 characters."]})
        result["display_name"] = name
    if not partial or "slug" in data:
        slug = data.get("slug", "").strip().lower()
        if not 3 <= len(slug) <= 63 or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
            raise InputError({"slug": ["Use 3–63 lowercase letters, numbers, or single hyphens."]})
        if slug in {"api", "admin", "login", "register", "preview", "new", "settings"}:
            raise InputError({"slug": ["This workspace address is reserved."]})
        result["slug"] = slug
    if not partial or "timezone" in data:
        zone = data.get("timezone", "UTC")
        try:
            if len(zone) > 64:
                raise ValueError
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise InputError(
                {"timezone": ["Choose a valid timezone, for example Asia/Kolkata."]}
            ) from None
        result["timezone"] = zone
    if not result:
        raise InputError({"body": ["Include at least one setting to update."]})
    return result


def serialize(org, membership):
    return {
        "id": str(org.id),
        "display_name": org.display_name,
        "slug": org.slug,
        "timezone": org.timezone,
        "state": org.state,
        "created_at": org.created_at.isoformat(),
        "membership": {"id": str(membership.id), "role": membership.role},
    }


def record(org, membership, action, summary):
    event = AuditEvent.objects.create(
        org=org,
        actor_membership_id=membership.id,
        action=action,
        resource_id=org.id,
        summary=summary,
    )
    # Durable capture is atomic with the mutation. Step 8 adds consumers/recovery.
    OutboxEvent.objects.create(
        org=org,
        audit_id=event.id,
        topic=action,
        payload={"audit_id": str(event.id), "resource_id": str(org.id)},
    )


def authenticated(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return error(request, "UNAUTHENTICATED", "Sign in to continue.", 401)
    return None


def unavailable(request):
    return error(request, "NOT_FOUND", "This organization is unavailable.", 404)


@endpoint("GET, POST", FIELDS)
def collection(request, data):
    denied = authenticated(request)
    if denied is not None:
        return denied
    if request.method == "GET":
        try:
            limit = int(request.GET.get("limit", 25))
            if not 1 <= limit <= 100:
                raise ValueError
            after = request.GET.get("cursor")
            if after:
                after = signing.loads(after, salt=f"org-list:{request.user.id}", max_age=86400)
                after = uuid.UUID(after)
        except (ValueError, signing.BadSignature):
            raise InputError({"cursor": ["Invalid page or page size. Refresh the list."]}) from None
        with tenant_transaction(request.user.id, discovery=True):
            memberships = Membership.objects.filter(
                user=request.user, state="active", org__state="active"
            )
            if after:
                memberships = memberships.filter(org_id__gt=after)
            rows = list(memberships.select_related("org").order_by("org_id")[: limit + 1])
            more = len(rows) > limit
            rows = rows[:limit]
            return JsonResponse(
                {
                    "results": [serialize(row.org, row) for row in rows],
                    "next_cursor": signing.dumps(
                        str(rows[-1].org_id), salt=f"org-list:{request.user.id}"
                    )
                    if more
                    else None,
                }
            )
    if not request.user.verified_at:
        return error(
            request, "EMAIL_NOT_VERIFIED", "Verify your email to create an organization.", 403
        )
    values = inputs(data)
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
        raise InputError(
            {
                "idempotency_key": [
                    "Send a unique Idempotency-Key of 16–128 letters, numbers or hyphens."
                ]
            }
        )
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    org_id = uuid.uuid4()
    try:
        with tenant_transaction(request.user.id, org_id):
            # Serialize creation retries for this actor, including simultaneous first requests.
            User.objects.select_for_update().get(pk=request.user.id)
            previous = CreationRequest.objects.filter(actor=request.user, key=key).first()
            if previous and previous.expires_at > timezone.now():
                if previous.payload_hash != digest:
                    return error(
                        request,
                        "IDEMPOTENCY_CONFLICT",
                        "This request key was already used for different settings.",
                        409,
                    )
                with tenant_transaction(request.user.id, previous.response["organization"]["id"]):
                    if not Organization.objects.filter(
                        pk=previous.response["organization"]["id"], state="active"
                    ).exists():
                        return unavailable(request)
                return JsonResponse(previous.response, status=201)
            if previous:
                previous.delete()
            member_id = uuid.uuid4()
            org = Organization.objects.create(
                id=org_id, created_by=request.user, owner_membership_id=member_id, **values
            )
            member = Membership.objects.create(
                id=member_id, org=org, user=request.user, role="Owner"
            )
            OrganizationUsage.objects.create(
                org=org,
                member_limit=settings.ORGANIZATION_MEMBER_LIMIT,
                project_limit=settings.ORGANIZATION_PROJECT_LIMIT,
                task_limit=settings.ORGANIZATION_TASK_LIMIT,
                storage_limit=settings.ORGANIZATION_STORAGE_LIMIT,
            )
            record(org, member, "organization.created", values)
            response = {"organization": serialize(org, member)}
            CreationRequest.objects.create(
                actor=request.user,
                key=key,
                payload_hash=digest,
                response=response,
                expires_at=timezone.now() + timedelta(hours=24),
            )
        return JsonResponse(response, status=201)
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.__cause__, "diag", None), "constraint_name", "")
            == "organizations_organization_slug_key"
        ):
            raise InputError({"slug": ["This workspace address is already taken."]}) from None
        raise


@endpoint("GET, PATCH", FIELDS)
def detail(request, data, org_id):
    denied = authenticated(request)
    if denied is not None:
        return denied
    with tenant_transaction(request.user.id, org_id):
        member = Membership.objects.filter(org_id=org_id, user=request.user, state="active").first()
        org = Organization.objects.filter(pk=org_id, state="active").first()
        if not member or not org:
            return unavailable(request)
        if request.method == "PATCH":
            if member.role not in ("Owner", "Admin"):
                return error(
                    request,
                    "FORBIDDEN",
                    "Only an Owner or Admin can change organization settings.",
                    403,
                )
            values = inputs(data, partial=True)
            try:
                with transaction.atomic():
                    org = Organization.objects.select_for_update().filter(pk=org_id).first()
                    if org is None:
                        return unavailable(request)
                    changed = {
                        key: value for key, value in values.items() if getattr(org, key) != value
                    }
                    if changed:
                        for key, value in changed.items():
                            setattr(org, key, value)
                        org.save(update_fields=list(changed))
                        record(org, member, "organization.updated", changed)
            except IntegrityError as exc:
                if (
                    getattr(getattr(exc.__cause__, "diag", None), "constraint_name", "")
                    == "organizations_organization_slug_key"
                ):
                    raise InputError(
                        {"slug": ["This workspace address is already taken."]}
                    ) from None
                raise
        return JsonResponse({"organization": serialize(org, member)})
