import hashlib
import json
import re
import uuid
from datetime import date, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from django.db.models import Count, F, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from accounts.views import InputError, endpoint, error
from organizations.context import tenant_transaction
from organizations.models import Membership, OrganizationUsage
from organizations.team import TeamError, actor_for, event, member_data, page
from organizations.views import authenticated

from .models import Project, ProjectMembership, Task, WriteRequest
from .services import (
    accessible_projects,
    clear_assignments,
    manager,
    project_members,
    visible_project,
    writable,
)

STATUSES = ["To Do", "In Progress", "Blocked", "Done"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
TASK_FIELDS = ("title", "description", "assignee_id", "priority", "due_date", "status")


def project_endpoint(methods, fields=()):
    def decorate(view):
        @endpoint(methods, fields, text_only=False)
        @wraps(view)
        def wrapped(request, data, org_id, **kwargs):
            denied = authenticated(request)
            if denied is not None:
                return denied
            try:
                with tenant_transaction(request.user.id, org_id):
                    org, actor = actor_for(request, org_id)
                    if request.method != "GET":
                        if actor.role == "Viewer":
                            # Resolve project visibility in the view before denying writes.
                            return view(request, data, org, actor, **kwargs)
                        org, actor = actor_for(request, org_id, lock=True)
                    return view(request, data, org, actor, **kwargs)
            except TeamError as exc:
                return error(request, exc.code, exc.message, exc.status)

        return wrapped

    return decorate


def text(data, key, maximum, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise InputError({key: [f"Use {'1' if required else '0'}–{maximum} characters."]})
    return value.strip() if required else value


def identifier(value, field):
    try:
        if not isinstance(value, str):
            raise ValueError
        return uuid.UUID(value)
    except ValueError:
        raise InputError({field: ["Choose a valid member."]}) from None


def local_today(org):
    return timezone.now().astimezone(ZoneInfo(org.timezone)).date()


def task_data(task, today, details=False):
    assignee = (
        Membership.objects.filter(org_id=task.org_id, pk=task.assignee_id)
        .select_related("user")
        .first()
        if task.assignee_id
        else None
    )
    result = {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "version": task.version,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "assignee_name": assignee.user.display_name
        if assignee and assignee.state == "active" and assignee.user.is_active
        else "Former member"
        if task.assignee_id
        else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "updated_at": task.updated_at.isoformat(),
        "overdue": bool(task.due_date and task.due_date < today and task.status != "Done"),
    }
    if details:
        result["description"] = task.description
    return result


def project_data(project):
    tasks = Task.objects.filter(org_id=project.org_id, project=project, deleted_at__isnull=True)
    counts = tasks.aggregate(total=Count("id"), done=Count("id", filter=Q(status="Done")))
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "archived_at": project.archived_at.isoformat() if project.archived_at else None,
        "created_at": project.created_at.isoformat(),
        "task_count": counts["total"],
        "completed_count": counts["done"],
    }


def idempotent(request, data, org, endpoint_name, create):
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
        raise InputError(
            {"idempotency_key": ["Send a unique Idempotency-Key of 16–128 characters."]}
        )
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    saved = WriteRequest.objects.filter(
        org=org, actor=request.user, endpoint=endpoint_name, key=key
    ).first()
    if saved and saved.expires_at > timezone.now():
        if saved.payload_hash != digest:
            raise TeamError(
                "IDEMPOTENCY_CONFLICT",
                "This request already completed with different details.",
                409,
            )
        return JsonResponse(saved.response, status=201)
    if saved:
        saved.delete()
    result = create()
    WriteRequest.objects.create(
        org=org,
        actor=request.user,
        endpoint=endpoint_name,
        key=key,
        payload_hash=digest,
        response=result,
        expires_at=timezone.now() + timedelta(hours=24),
    )
    return JsonResponse(result, status=201)


@project_endpoint("GET, POST", ("name", "description"))
def projects(request, data, org, actor):
    if request.method == "GET":
        query = accessible_projects(org.id, actor)
        state = request.GET.get("state", "active")
        if state not in {"active", "archived", "all"}:
            raise InputError({"state": ["Choose active, archived or all."]})
        if state != "all":
            query = query.filter(archived_at__isnull=state == "active")
        result = page(request, query, f"{org.id}:projects:{state}", project_data)
        result["role"] = actor.role
        if actor.role in {"Owner", "Admin"}:
            usage = OrganizationUsage.objects.get(org=org)
            result["usage"] = {
                "active_projects": usage.active_projects,
                "project_limit": usage.project_limit,
            }
        return JsonResponse(result)
    manager(actor)
    name, description = text(data, "name", 120, True), text(data, "description", 5000)

    def create():
        usage = OrganizationUsage.objects.get(org=org)
        if usage.active_projects >= usage.project_limit:
            raise TeamError("PROJECT_LIMIT", "Archive a project to free a project place.", 409)
        project = Project.objects.create(org=org, name=name, description=description)
        ProjectMembership.objects.create(org=org, project=project, membership=actor)
        usage.active_projects += 1
        usage.save(update_fields=["active_projects"])
        event(org, actor, "project.created", project.id, {})
        return {"project": project_data(project)}

    return idempotent(request, data, org, "projects", create)


@project_endpoint("GET, PATCH", ("name", "description"))
def project_detail(request, data, org, actor, project_id):
    project = visible_project(org.id, actor, project_id)
    if request.method == "PATCH":
        manager(actor)
        writable(project, actor)
        if not data:
            raise InputError({"body": ["Include a setting to update."]})
        if "name" in data:
            project.name = text(data, "name", 120, True)
        if "description" in data:
            project.description = text(data, "description", 5000)
        project.updated_at = timezone.now()
        project.save(update_fields=[*data, "updated_at"])
        event(org, actor, "project.updated", project.id, {"fields": sorted(data)})
    return JsonResponse({"project": project_data(project), "role": actor.role})


@project_endpoint("POST")
def archive(request, data, org, actor, project_id, restore=False):
    project = visible_project(org.id, actor, project_id)
    manager(actor)
    if bool(project.archived_at) == restore:
        usage = OrganizationUsage.objects.get(org=org)
        if restore and usage.active_projects >= usage.project_limit:
            raise TeamError(
                "PROJECT_LIMIT", "Archive another project before restoring this one.", 409
            )
        usage.active_projects += 1 if restore else -1
        usage.save(update_fields=["active_projects"])
        project.archived_at = None if restore else timezone.now()
        project.updated_at = timezone.now()
        project.save(update_fields=["archived_at", "updated_at"])
        event(org, actor, "project.restored" if restore else "project.archived", project.id, {})
    return JsonResponse({"project": project_data(project)})


@project_endpoint("GET, POST", ("membership_id",))
def members(request, data, org, actor, project_id):
    project = visible_project(org.id, actor, project_id)
    if request.method == "GET":
        return JsonResponse(
            page(
                request,
                project_members(org.id, project.id),
                f"{org.id}:{project.id}:members",
                member_data,
            )
        )
    manager(actor)
    writable(project, actor)
    member_id = identifier(data.get("membership_id"), "membership_id")
    member = Membership.objects.filter(
        org=org, pk=member_id, state="active", user__is_active=True
    ).first()
    if not member:
        raise InputError({"membership_id": ["Choose an active member of this organization."]})
    link, created = ProjectMembership.objects.get_or_create(
        org=org, project=project, membership=member
    )
    if created or link.state != "active":
        link.state = "active"
        link.save(update_fields=["state"])
        event(org, actor, "project.member_added", project.id, {"membership_id": str(member.id)})
    return JsonResponse({"message": "Project member added."})


@project_endpoint("DELETE")
def member_remove(request, data, org, actor, project_id, member_id):
    project = visible_project(org.id, actor, project_id)
    manager(actor)
    writable(project, actor)
    member = project_members(org.id, project.id).filter(pk=member_id).first()
    if not member:
        raise TeamError("NOT_FOUND", "This project member is unavailable.", 404)
    count = clear_assignments(org, actor, member, project_id=project.id)
    event(
        org,
        actor,
        "project.member_removed",
        project.id,
        {"membership_id": str(member.id), "unassigned": count},
    )
    return HttpResponse(status=204)


def task_values(data, org, project, current=None):
    values = {}
    if current is None or "title" in data:
        values["title"] = text(data, "title", 200, True)
    if "description" in data:
        values["description"] = text(data, "description", 20000)
    for field, allowed in [("status", STATUSES), ("priority", PRIORITIES)]:
        if field in data:
            if not isinstance(data[field], str) or data[field] not in allowed:
                raise InputError({field: ["Choose one of: " + ", ".join(allowed)]})
            values[field] = data[field]
    if "due_date" in data:
        value = data["due_date"]
        try:
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            ):
                raise ValueError
            values["due_date"] = date.fromisoformat(value) if value else None
        except ValueError:
            raise InputError({"due_date": ["Use a valid YYYY-MM-DD date or null."]}) from None
    if "assignee_id" in data:
        values["assignee_id"] = (
            identifier(data["assignee_id"], "assignee_id")
            if data["assignee_id"] is not None
            else None
        )
    assignee = values.get("assignee_id", current.assignee_id if current else None)
    # Completed tasks may retain historical attribution after offboarding.
    # Reopening requires a currently eligible assignee or explicitly clearing it.
    historical_done = (
        current
        and current.status == "Done"
        and values.get("status", current.status) == "Done"
        and "assignee_id" not in data
    )
    if (
        assignee
        and not historical_done
        and not project_members(org.id, project.id)
        .exclude(role="Viewer")
        .filter(pk=assignee)
        .exists()
    ):
        raise InputError(
            {"assignee_id": ["Choose an active, non-viewer project member, or leave unassigned."]}
        )
    status = values.get("status", current.status if current else "To Do")
    if not current or status != current.status:
        values["completed_at"] = timezone.now() if status == "Done" else None
    return values


def filter_tasks(request, queryset, org, actor):
    filters = {}
    for field, choices in [("status", STATUSES), ("priority", PRIORITIES)]:
        value = request.GET.get(field)
        if value:
            if value not in choices:
                raise InputError({field: ["Invalid filter."]})
            filters[field] = value
    assignee = request.GET.get("assignee")
    if assignee:
        filters["assignee_id"] = (
            None
            if assignee == "unassigned"
            else actor.id
            if assignee == "me"
            else identifier(assignee, "assignee")
        )
    queryset = queryset.filter(**filters)
    overdue = request.GET.get("overdue", "false")
    if overdue not in {"true", "false"}:
        raise InputError({"overdue": ["Choose true or false."]})
    if overdue == "true":
        queryset = queryset.filter(due_date__lt=local_today(org)).exclude(status="Done")
    return queryset


@project_endpoint("GET, POST", TASK_FIELDS)
def tasks(request, data, org, actor, project_id):
    project = visible_project(org.id, actor, project_id)
    today = local_today(org)
    if request.method == "GET":
        query = filter_tasks(
            request,
            Task.objects.filter(org=org, project=project, deleted_at__isnull=True),
            org,
            actor,
        )
        filters = json.dumps(
            sorted((k, v) for k, v in request.GET.items() if k not in {"cursor", "limit"})
        )
        return JsonResponse(
            page(
                request,
                query,
                f"{org.id}:{project.id}:tasks:{filters}",
                lambda task: task_data(task, today),
            )
        )
    writable(project, actor)
    values = task_values(data, org, project)

    def create():
        usage = OrganizationUsage.objects.get(org=org)
        if usage.active_tasks >= usage.task_limit:
            raise TeamError("TASK_LIMIT", "This organization has reached its task limit.", 409)
        task = Task.objects.create(org=org, project=project, created_by_id=actor.id, **values)
        usage.active_tasks += 1
        usage.save(update_fields=["active_tasks"])
        event(org, actor, "task.created", task.id, {"project_id": str(project.id)})
        return {"task": task_data(task, today, True)}

    return idempotent(request, data, org, f"{project.id}:tasks", create)


@project_endpoint("GET, PATCH, DELETE", (*TASK_FIELDS, "version"))
def task_detail(request, data, org, actor, task_id):
    task = Task.objects.filter(
        org=org,
        pk=task_id,
        deleted_at__isnull=True,
        project_id__in=accessible_projects(org.id, actor).values("id"),
    ).first()
    if not task:
        raise TeamError("NOT_FOUND", "This task is unavailable.", 404)
    if request.method == "GET":
        return JsonResponse({"task": task_data(task, local_today(org), True)})
    project = visible_project(org.id, actor, task.project_id)
    writable(project, actor)
    if request.method == "DELETE":
        manager(actor)
        if set(data) != {"version"}:
            raise InputError({"body": ["Send only the current task version."]})
    version = data.get("version")
    if type(version) is not int or version < 1:
        raise InputError({"version": ["Send the current task version as a positive integer."]})
    if task.version != version:
        return error(
            request,
            "VERSION_CONFLICT",
            "Task changed. Refresh and retry.",
            409,
            {"current_version": task.version},
        )
    if request.method == "DELETE":
        values = {"deleted_at": timezone.now()}
    else:
        if set(data) == {"version"}:
            raise InputError({"body": ["Include a task field to update."]})
        values = task_values(data, org, project, task)
    count = Task.objects.filter(
        org=org, pk=task.id, version=version, deleted_at__isnull=True
    ).update(**values, version=F("version") + 1, updated_at=timezone.now())
    if not count:
        current = Task.objects.get(org=org, pk=task.id)
        return error(
            request,
            "VERSION_CONFLICT",
            "Task changed. Refresh and retry.",
            409,
            {"current_version": current.version},
        )
    event(
        org,
        actor,
        "task.deleted" if request.method == "DELETE" else "task.updated",
        task.id,
        {
            "fields": sorted(k for k in data if k != "version"),
            "version": version + 1,
            "project_id": str(project.id),
        },
    )
    if request.method == "DELETE":
        usage = OrganizationUsage.objects.get(org=org)
        usage.active_tasks -= 1
        usage.save(update_fields=["active_tasks"])
        return HttpResponse(status=204)
    task.refresh_from_db()
    return JsonResponse({"task": task_data(task, local_today(org), True)})


@project_endpoint("GET")
def dashboard(request, data, org, actor):
    projects = accessible_projects(org.id, actor).filter(archived_at__isnull=True)
    tasks = Task.objects.filter(
        org=org, project_id__in=projects.values("id"), deleted_at__isnull=True
    )
    today = local_today(org)
    mine = tasks.filter(assignee_id=actor.id).exclude(status="Done")
    return JsonResponse(
        {
            "counts": {
                "projects": projects.count(),
                "assigned": mine.count(),
                "blocked": tasks.filter(status="Blocked").count(),
                "overdue": tasks.filter(due_date__lt=today).exclude(status="Done").count(),
            },
            "projects": [
                project_data(project) for project in projects.order_by("-updated_at", "id")[:5]
            ],
            "tasks": [
                task_data(task, today)
                for task in mine.order_by(F("due_date").asc(nulls_last=True), "id")[:5]
            ],
            "timezone": org.timezone,
        }
    )
