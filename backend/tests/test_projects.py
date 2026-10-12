import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client

from organizations.context import tenant_transaction
from organizations.models import Membership, OrganizationUsage, OutboxEvent
from projects.models import Project, ProjectMembership, Task
from tests import test_organizations as base
from tests.test_team import seed_member

app_role = base.app_role
owner = base.owner
signed_in = base.signed_in
restricted = base.restricted
pytestmark = pytest.mark.django_db(transaction=True)


def call(client, role, path, data=None, method="GET", key=None):
    with restricted(role):
        return client.generic(
            method,
            "/api/v1/orgs/" + path,
            data=json.dumps(data or {}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
        )


@pytest.fixture
def org(signed_in, app_role):
    return base.make_org(signed_in, app_role)


def project(client, role, org, name="Website redesign"):
    response = call(client, role, org["id"] + "/projects/", {"name": name}, "POST")
    assert response.status_code == 201, response.content
    return response.json()["project"]


@pytest.fixture
def work(signed_in, app_role, org):
    return project(signed_in, app_role, org)


def task(client, role, org, work, **fields):
    response = call(
        client,
        role,
        f"{org['id']}/projects/{work['id']}/tasks/",
        {"title": "Build the homepage", **fields},
        "POST",
    )
    assert response.status_code == 201, response.content
    return response.json()["task"]


def add(client, role, org, work, member):
    response = call(
        client,
        role,
        f"{org['id']}/projects/{work['id']}/members/",
        {"membership_id": str(member.id)},
        "POST",
    )
    assert response.status_code == 200, response.content


def test_task_lifecycle_version_soft_delete(signed_in, app_role, org, work):
    item = task(
        signed_in, app_role, org, work, description="x" * 20000, assignee_id=org["membership"]["id"]
    )
    route = f"{org['id']}/tasks/{item['id']}/"
    done = call(signed_in, app_role, route, {"status": "Done", "version": 1}, "PATCH")
    assert done.status_code == 200, done.content
    assert done.json()["task"]["completed_at"]
    conflict = call(signed_in, app_role, route, {"title": "Old edit", "version": 1}, "PATCH")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"]["current_version"] == 2
    reopened = call(signed_in, app_role, route, {"status": "Blocked", "version": 2}, "PATCH")
    assert reopened.json()["task"]["completed_at"] is None
    assert reopened.json()["task"]["version"] == 3
    assert call(signed_in, app_role, route, {"version": 3}, "DELETE").status_code == 204
    assert call(signed_in, app_role, route).status_code == 404
    row = Task.objects.get(pk=item["id"])
    assert row.deleted_at and row.version == 4
    assert OrganizationUsage.objects.get(org_id=org["id"]).active_tasks == 0
    assert OutboxEvent.objects.filter(topic="task.deleted").exists()


@pytest.mark.parametrize(
    "role,linked,read,write",
    [
        ("Admin", False, 200, 201),
        ("Member", False, 404, 404),
        ("Member", True, 200, 201),
        ("Viewer", True, 200, 403),
        ("Viewer", False, 404, 404),
    ],
)
def test_project_role_matrix(signed_in, app_role, org, work, role, linked, read, write):
    member, client = seed_member(org, role)
    existing = task(signed_in, app_role, org, work)
    if linked:
        add(signed_in, app_role, org, work, member)
    root = f"{org['id']}/projects/{work['id']}/"
    assert call(client, app_role, root).status_code == read
    assert call(client, app_role, f"{org['id']}/tasks/{existing['id']}/").status_code == read
    assert call(client, app_role, root + "tasks/", {"title": "New"}, "POST").status_code == write
    assert call(
        client, app_role, f"{org['id']}/projects/", {"name": "Private"}, "POST"
    ).status_code == (201 if role == "Admin" else 403)
    if read == 200 and role != "Admin":
        assert (
            call(
                client, app_role, f"{org['id']}/tasks/{existing['id']}/", {"version": 1}, "DELETE"
            ).status_code
            == 403
        )
        assert call(client, app_role, root + "archive/", {}, "POST").status_code == 403
    if read == 404:
        assert call(client, app_role, f"{org['id']}/projects/").json()["results"] == []
        assert call(client, app_role, f"{org['id']}/dashboard/").json()["counts"]["projects"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"description": "x" * 20001},
        {"description": []},
        {"status": "In Review"},
        {"status": []},
        {"priority": "Unknown"},
        {"due_date": "2026-02-30"},
        {"due_date": 3},
        {"assignee_id": {}},
        {"org_id": "forged"},
        {"project_id": "forged"},
    ],
)
def test_task_validation(signed_in, app_role, org, work, payload):
    route = f"{org['id']}/projects/{work['id']}/tasks/"
    response = call(signed_in, app_role, route, {"title": "Valid", **payload}, "POST")
    assert response.status_code == 400, response.content
    assert Task.objects.count() == 0


def test_assignee_rules(signed_in, app_role, org, work):
    viewer, _ = seed_member(org, "Viewer", "viewer@example.test")
    member, _ = seed_member(org, "Member")
    route = f"{org['id']}/projects/{work['id']}/tasks/"
    add(signed_in, app_role, org, work, viewer)
    for assignee in [viewer.id, member.id, uuid.uuid4()]:
        assert (
            call(
                signed_in, app_role, route, {"title": "Task", "assignee_id": str(assignee)}, "POST"
            ).status_code
            == 400
        )
    add(signed_in, app_role, org, work, member)
    assert task(signed_in, app_role, org, work, assignee_id=str(member.id))["assignee_id"] == str(
        member.id
    )


def test_archived_project_is_read_only_and_quota_restore(signed_in, app_role, org, work):
    item = task(signed_in, app_role, org, work)
    member, _ = seed_member(org)
    root = f"{org['id']}/projects/{work['id']}/"
    assert call(signed_in, app_role, root + "archive/", {}, "POST").status_code == 200
    usage = OrganizationUsage.objects.get(org_id=org["id"])
    assert (usage.active_projects, usage.active_tasks) == (0, 1)
    assert call(signed_in, app_role, root).status_code == 200
    for path, data, method in [
        (root, {"name": "Edit"}, "PATCH"),
        (root + "tasks/", {"title": "New"}, "POST"),
        (root + "members/", {"membership_id": str(member.id)}, "POST"),
        (f"{org['id']}/tasks/{item['id']}/", {"status": "Done", "version": 1}, "PATCH"),
        (f"{org['id']}/tasks/{item['id']}/", {"version": 1}, "DELETE"),
    ]:
        assert call(signed_in, app_role, path, data, method).status_code == 409
    usage.project_limit = 1
    usage.save()
    other = project(signed_in, app_role, org, "Other")
    assert call(signed_in, app_role, root + "restore/", {}, "POST").status_code == 409
    call(signed_in, app_role, f"{org['id']}/projects/{other['id']}/archive/", {}, "POST")
    assert call(signed_in, app_role, root + "restore/", {}, "POST").status_code == 200


@pytest.mark.parametrize("action", ["offboard", "project_remove", "viewer"])
def test_access_changes_unassign_and_bump_version(signed_in, app_role, org, work, action):
    member, client = seed_member(org)
    add(signed_in, app_role, org, work, member)
    opened = task(client, app_role, org, work, assignee_id=str(member.id))
    done = task(client, app_role, org, work, assignee_id=str(member.id), status="Done")
    root = f"{org['id']}/projects/{work['id']}/"
    if action == "offboard":
        call(signed_in, app_role, root + "archive/", {}, "POST")
        response = call(signed_in, app_role, f"{org['id']}/members/{member.id}/", {}, "DELETE")
    elif action == "viewer":
        response = call(
            signed_in, app_role, f"{org['id']}/members/{member.id}/", {"role": "Viewer"}, "PATCH"
        )
    else:
        response = call(signed_in, app_role, root + f"members/{member.id}/", {}, "DELETE")
    assert response.status_code in [200, 204], response.content
    row = Task.objects.get(pk=opened["id"])
    assert row.assignee_id is None and row.version == 2
    assert Task.objects.get(pk=done["id"]).assignee_id == member.id
    assert call(client, app_role, root).status_code == (200 if action == "viewer" else 404)
    assert ProjectMembership.objects.get(project_id=work["id"], membership=member).state == (
        "active" if action == "viewer" else "removed"
    )


def test_timezone_filters_and_cursor(signed_in, app_role, org, work):
    from organizations.models import Organization

    Organization.objects.filter(pk=org["id"]).update(timezone="Asia/Kolkata")
    now = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # Oct 13 locally.
    with patch("projects.views.timezone.now", return_value=now):
        first = task(
            signed_in,
            app_role,
            org,
            work,
            due_date="2026-10-12",
            priority="High",
            assignee_id=org["membership"]["id"],
        )
        task(signed_in, app_role, org, work, due_date="2026-10-13", status="Done")
        route = f"{org['id']}/projects/{work['id']}/tasks/"
        overdue = call(
            signed_in, app_role, route + "?overdue=true&assignee=me&priority=High"
        ).json()
        assert [x["id"] for x in overdue["results"]] == [first["id"]]
        assert overdue["results"][0]["overdue"]
        page = call(signed_in, app_role, route + "?limit=1").json()
        assert page["next_cursor"]
        assert (
            call(signed_in, app_role, route + "?cursor=" + page["next_cursor"]).json()[
                "next_cursor"
            ]
            is None
        )
        assert (
            call(
                signed_in, app_role, route + "?status=Done&cursor=" + page["next_cursor"]
            ).status_code
            == 400
        )


def race(role, jobs):
    def run(job):
        try:
            return call(job[0], role, job[1], job[2], job[3], key=job[4]).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(run, jobs))


def test_concurrent_task_edit(signed_in, owner, app_role, org, work):
    item = task(signed_in, app_role, org, work)
    other = Client()
    other.force_login(owner)
    route = f"{org['id']}/tasks/{item['id']}/"
    jobs = [
        (client, route, {"version": 1, "status": status}, "PATCH", None)
        for client, status in [(signed_in, "Done"), (other, "Blocked")]
    ]
    assert race(app_role, jobs) == [200, 409]
    assert Task.objects.get(pk=item["id"]).version == 2


@pytest.mark.parametrize("kind", ["projects", "tasks"])
def test_last_quota_place_race(signed_in, owner, app_role, org, work, kind):
    usage = OrganizationUsage.objects.get(org_id=org["id"])
    usage.project_limit, usage.task_limit = 2, 1
    usage.save()
    other = Client()
    other.force_login(owner)
    route = (
        f"{org['id']}/projects/"
        if kind == "projects"
        else f"{org['id']}/projects/{work['id']}/tasks/"
    )
    payload = {"name": "Project"} if kind == "projects" else {"title": "Task"}
    assert race(
        app_role,
        [(client, route, payload, "POST", uuid.uuid4().hex) for client in [signed_in, other]],
    ) == [201, 409]


def test_idempotency_and_atomic_rollback(signed_in, app_role, org, work):
    route = f"{org['id']}/projects/{work['id']}/tasks/"
    key = uuid.uuid4().hex
    first = call(signed_in, app_role, route, {"title": "Original"}, "POST", key)
    second = call(signed_in, app_role, route, {"title": "Original"}, "POST", key)
    assert first.json() == second.json()
    assert call(signed_in, app_role, route, {"title": "Other"}, "POST", key).status_code == 409
    with patch("organizations.team.OutboxEvent.objects.create", side_effect=IntegrityError):
        assert call(signed_in, app_role, route, {"title": "Rollback"}, "POST").status_code == 503
    assert Task.objects.count() == OrganizationUsage.objects.get(org_id=org["id"]).active_tasks == 1


def test_cross_org_same_user_roles_and_composite_fk(signed_in, owner, app_role, org, work):
    member, client = seed_member(org, "Member")
    other_org = base.make_org(client, app_role, "other-org")
    other_project = project(client, app_role, other_org)
    item = task(signed_in, app_role, org, work)
    assert call(client, app_role, f"{org['id']}/tasks/{item['id']}/").status_code == 404
    assert call(client, app_role, f"{other_org['id']}/tasks/{item['id']}/").status_code == 404
    with restricted(app_role):
        assert Task.objects.count() == Project.objects.count() == 0
        with tenant_transaction(member.user_id, other_org["id"]):
            assert not Task.objects.filter(pk=item["id"]).exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        ProjectMembership.objects.create(
            org_id=org["id"], project_id=other_project["id"], membership=member
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.filter(pk=item["id"]).update(assignee_id=member.id)
    add(signed_in, app_role, org, work, member)
    # This user is Owner in B but only Viewer in A.
    call(signed_in, app_role, f"{org['id']}/members/{member.id}/", {"role": "Viewer"}, "PATCH")
    assert (
        call(
            client,
            app_role,
            f"{org['id']}/tasks/{item['id']}/",
            {"version": 1, "status": "Done"},
            "PATCH",
        ).status_code
        == 403
    )


def test_offboard_rollback_and_csrf(signed_in, owner, app_role, org, work):
    member, _ = seed_member(org)
    add(signed_in, app_role, org, work, member)
    item = task(signed_in, app_role, org, work, assignee_id=str(member.id))
    with patch("projects.services.event", side_effect=IntegrityError):
        assert (
            call(signed_in, app_role, f"{org['id']}/members/{member.id}/", {}, "DELETE").status_code
            == 503
        )
    assert Membership.objects.get(pk=member.id).state == "active"
    assert Task.objects.get(pk=item["id"]).assignee_id == member.id
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(owner)
    assert (
        call(csrf, app_role, f"{org['id']}/projects/", {"name": "No CSRF"}, "POST").status_code
        == 403
    )
