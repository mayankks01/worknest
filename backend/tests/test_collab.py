import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone
from PIL import Image
from pypdf import PdfWriter

from collab import validation
from collab.models import Attachment, Comment
from collab.tasks import process_attachment
from organizations.context import tenant_transaction
from organizations.models import AuditEvent, Membership, OrganizationUsage
from tests import test_organizations as base
from tests import test_projects as project_tests
from tests.test_team import seed_member

pytestmark = pytest.mark.django_db(transaction=True)
app_role = base.app_role
owner = base.owner
signed_in = base.signed_in
org = project_tests.org
work = project_tests.work
call = project_tests.call


@pytest.fixture
def item(signed_in, app_role, org, work):
    return project_tests.task(signed_in, app_role, org, work)


@pytest.fixture
def store():
    objects = {}

    def put(key, content):
        assert not connection.in_atomic_block
        objects[key] = content

    def read(key, size, digest):
        assert not connection.in_atomic_block
        return io.BytesIO(objects[key])

    def delete(key):
        assert not connection.in_atomic_block
        objects.pop(key, None)

    with (
        patch("collab.storage.put", side_effect=put),
        patch("collab.storage.read", side_effect=read),
        patch("collab.storage.delete", side_effect=delete),
        patch("collab.views.publish"),
        patch("collab.validation.scan", return_value=True),
    ):
        yield objects


def upload(client, app_role, org, item, content=b"Hello team", name="brief.txt", key=None, **extra):
    with base.restricted(app_role):
        return client.post(
            f"/api/v1/orgs/{org['id']}/tasks/{item['id']}/attachments/",
            {"file": SimpleUploadedFile(name, content, "application/x-forged"), **extra},
            HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
        )


def process(app_role, row):
    Attachment.objects.filter(pk=row.id).update(next_attempt_at=timezone.now())
    with base.restricted(app_role):
        process_attachment(str(row.org_id), str(row.id))
    row.refresh_from_db()


def test_comments_plain_text_author_permissions_and_oldest_paging(
    signed_in, app_role, org, work, item
):
    route = f"{org['id']}/tasks/{item['id']}/comments/"
    payload = {"body": "<script>alert(1)</script> Plain text, not HTML."}
    key = uuid.uuid4().hex
    first = call(signed_in, app_role, route, payload, "POST", key=key)
    assert first.status_code == 201
    assert call(signed_in, app_role, route, payload, "POST", key=key).json() == first.json()
    comment = first.json()["comment"]
    assert comment["body"] == payload["body"]
    second = call(signed_in, app_role, route, {"body": "Second"}, "POST").json()["comment"]
    page = call(signed_in, app_role, route + "?limit=1").json()
    assert page["results"][0]["id"] == comment["id"]
    assert (
        call(signed_in, app_role, route + "?limit=1&cursor=" + page["next_cursor"]).json()[
            "results"
        ][0]["id"]
        == second["id"]
    )
    member, client = seed_member(org, "Member")
    project_tests.add(signed_in, app_role, org, work, member)
    detail = f"{org['id']}/comments/{comment['id']}/"
    assert (
        call(client, app_role, detail, {"body": "Not mine", "version": 1}, "PATCH").status_code
        == 403
    )
    assert call(client, app_role, detail, {"version": 1}, "DELETE").status_code == 403
    own = call(client, app_role, route, {"body": "My comment"}, "POST").json()["comment"]
    own_path = f"{org['id']}/comments/{own['id']}/"
    edited = call(client, app_role, own_path, {"body": "Edited", "version": 1}, "PATCH")
    assert edited.status_code == 200
    assert edited.json()["comment"]["created_at"] == own["created_at"]
    assert (
        call(client, app_role, own_path, {"body": "Stale", "version": 1}, "PATCH").status_code
        == 409
    )
    assert (
        call(
            signed_in, app_role, own_path, {"body": "Owner edit", "version": 2}, "PATCH"
        ).status_code
        == 403
    )
    assert call(signed_in, app_role, own_path, {"version": 2}, "DELETE").status_code == 204
    assert Comment.objects.get(pk=own["id"]).body == ""


@pytest.mark.parametrize(
    "payload",
    [{"body": ""}, {"body": "x" * 5001}, {"body": []}, {"body": "Hi", "org_id": "forged"}],
)
def test_comment_validation(signed_in, app_role, org, item, payload):
    assert (
        call(
            signed_in, app_role, f"{org['id']}/tasks/{item['id']}/comments/", payload, "POST"
        ).status_code
        == 400
    )


def test_attachment_quarantine_content_and_private_download(signed_in, app_role, org, item, store):
    response = upload(signed_in, app_role, org, item, name="../client brief.txt")
    assert response.status_code == 202, response.content
    row = Attachment.objects.get()
    assert row.filename == "client brief.txt" and row.state == "queued"
    assert row.object_key.startswith(f"orgs/{org['id']}/attachments/")
    route = f"{org['id']}/attachments/{row.id}/download/"
    assert call(signed_in, app_role, route).status_code == 409
    process(app_role, row)
    assert row.state == "available" and row.scanned_at
    response = call(signed_in, app_role, route)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"Hello team"
    assert response["Content-Type"] == "text/plain"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 10


@pytest.mark.parametrize(
    "name,content,expected",
    [("bad.exe", b"hello", 400), ("empty.txt", b"", 400), ("huge.txt", b"x" * 20, 413)],
)
def test_upload_rejections(signed_in, app_role, org, item, store, name, content, expected):
    with override_settings(ATTACHMENT_MAX_BYTES=10):
        response = upload(signed_in, app_role, org, item, name=name, content=content)
    assert response.status_code == expected, response.content
    assert not Attachment.objects.exists()


def test_wrong_content_and_malware_rejected_then_cleanup_idempotent(
    signed_in, app_role, org, item, store
):
    assert upload(signed_in, app_role, org, item, name="fake.png").status_code == 202
    row = Attachment.objects.get()
    process(app_role, row)
    assert row.state == "rejected" and row.reason == "INVALID_CONTENT"
    assert row.cleanup_pending and row.reserved_bytes == 10
    process(app_role, row)
    process(app_role, row)
    assert row.reserved_bytes == 0 and not store
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 0
    upload(signed_in, app_role, org, item)
    row = Attachment.objects.exclude(pk=row.id).get()
    with patch("collab.validation.scan", return_value=False):
        process(app_role, row)
    assert row.state == "rejected" and row.reason == "MALWARE_DETECTED"
    assert (
        call(signed_in, app_role, f"{org['id']}/attachments/{row.id}/download/").status_code == 409
    )


def test_scanner_outage_bounded_retries_and_failed_cleanup_retains_quota(
    signed_in, app_role, org, item, store
):
    upload(signed_in, app_role, org, item)
    row = Attachment.objects.get()
    with patch("collab.validation.scan", side_effect=OSError):
        for _ in range(5):
            process(app_role, row)
    assert row.state == "failed" and row.scan_attempts == 5 and row.cleanup_pending
    with patch("collab.storage.delete", side_effect=OSError):
        for _ in range(6):
            process(app_role, row)
    assert row.cleanup_attempts == 5 and row.reserved_bytes == 10
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 10


def test_upload_failure_and_abandoned_reservation_cleanup(signed_in, app_role, org, item, store):
    with patch("collab.storage.put", side_effect=OSError):
        assert upload(signed_in, app_role, org, item).status_code == 503
    row = Attachment.objects.get()
    assert row.cleanup_pending
    process(app_role, row)
    assert row.reserved_bytes == 0
    upload(signed_in, app_role, org, item)
    other = Attachment.objects.exclude(pk=row.id).get()
    Attachment.objects.filter(pk=other.id).update(state="uploading")
    process(app_role, other)
    assert other.state == "failed" and other.reserved_bytes == 0


def test_attachment_idempotency_and_concurrent_storage_quota(
    signed_in, owner, app_role, org, item, store
):
    key = uuid.uuid4().hex
    a = upload(signed_in, app_role, org, item, key=key)
    b = upload(signed_in, app_role, org, item, key=key)
    assert a.json()["attachment"]["id"] == b.json()["attachment"]["id"]
    assert upload(signed_in, app_role, org, item, key=key, content=b"changed").status_code == 409
    OrganizationUsage.objects.filter(org_id=org["id"]).update(storage_limit=20)
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda client: upload(client, app_role, org, item).status_code, clients)
        )
    assert sorted(results) == [202, 409]
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 20


@pytest.mark.parametrize(
    "role,linked,read,write",
    [
        ("Member", True, 200, 202),
        ("Member", False, 404, 404),
        ("Viewer", True, 200, 403),
        ("Viewer", False, 404, 404),
    ],
)
def test_collaboration_project_role_matrix(
    signed_in, app_role, org, work, item, store, role, linked, read, write
):
    member, client = seed_member(org, role)
    if linked:
        project_tests.add(signed_in, app_role, org, work, member)
    root = f"{org['id']}/tasks/{item['id']}/"
    assert call(client, app_role, root + "comments/").status_code == read
    assert call(client, app_role, root + "attachments/").status_code == read
    assert upload(client, app_role, org, item).status_code == write


def test_revoked_uploader_cannot_promote_file_worker_is_scoped(
    signed_in, app_role, org, work, item, store
):
    member, client = seed_member(org, "Member")
    project_tests.add(signed_in, app_role, org, work, member)
    upload(client, app_role, org, item)
    row = Attachment.objects.get()
    with base.restricted(app_role):
        process_attachment(str(uuid.uuid4()), str(row.id))
    row.refresh_from_db()
    assert row.state == "queued"
    call(signed_in, app_role, f"{org['id']}/members/{member.id}/", {}, "DELETE")
    process(app_role, row)
    assert row.state != "available"
    assert call(client, app_role, f"{org['id']}/attachments/{row.id}/download/").status_code == 404


def test_live_download_recheck_and_soft_deleted_task_hidden(
    signed_in, app_role, org, work, item, store
):
    member, client = seed_member(org, "Member")
    project_tests.add(signed_in, app_role, org, work, member)
    upload(signed_in, app_role, org, item)
    row = Attachment.objects.get()
    process(app_role, row)

    def revoked(*args):
        # Simulate a committed concurrent permission change during storage I/O.
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        Membership.objects.filter(pk=member.id).update(state="removed")
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE "{app_role}"')
        return io.BytesIO(b"Hello team")

    with patch("collab.storage.read", side_effect=revoked):
        assert (
            call(client, app_role, f"{org['id']}/attachments/{row.id}/download/").status_code == 404
        )
    call(signed_in, app_role, f"{org['id']}/tasks/{item['id']}/", {"version": 1}, "DELETE")
    assert (
        call(signed_in, app_role, f"{org['id']}/attachments/{row.id}/download/").status_code == 404
    )


def test_rename_delete_permissions_archive_and_counter_cleanup(
    signed_in, app_role, org, work, item, store
):
    member, client = seed_member(org, "Member")
    project_tests.add(signed_in, app_role, org, work, member)
    upload(client, app_role, org, item)
    row = Attachment.objects.get()
    process(app_role, row)
    path = f"{org['id']}/attachments/{row.id}/"
    assert call(signed_in, app_role, path, {"filename": "new.txt"}, "PATCH").status_code == 403
    assert call(client, app_role, path, {"filename": "new.pdf"}, "PATCH").status_code == 400
    assert call(client, app_role, path, {"filename": "new.txt"}, "PATCH").status_code == 200
    call(signed_in, app_role, f"{org['id']}/projects/{work['id']}/archive/", {}, "POST")
    assert upload(client, app_role, org, item).status_code == 409
    assert call(client, app_role, path, {}, "DELETE").status_code == 409
    assert (
        call(
            client, app_role, f"{org['id']}/tasks/{item['id']}/comments/", {"body": "No"}, "POST"
        ).status_code
        == 409
    )
    call(signed_in, app_role, f"{org['id']}/projects/{work['id']}/restore/", {}, "POST")
    assert call(signed_in, app_role, path, {}, "DELETE").status_code == 204
    assert call(client, app_role, path + "download/").status_code == 404
    process(app_role, row)
    assert row.state == "deleted" and row.reserved_bytes == 0


def test_rls_composite_constraints_and_atomic_reservation(signed_in, app_role, org, item, store):
    other_user = base.User.objects.create_user(
        "outsider@example.test", "test-password-1234", verified_at=timezone.now()
    )
    other_client = Client()
    other_client.force_login(other_user)
    other = base.make_org(other_client, app_role, "outside")
    with patch("collab.views.event", side_effect=IntegrityError):
        assert upload(signed_in, app_role, org, item).status_code == 503
    assert not Attachment.objects.exists() and not store
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 0
    upload(signed_in, app_role, org, item)
    row = Attachment.objects.get()
    assert (
        call(other_client, app_role, f"{other['id']}/attachments/{row.id}/download/").status_code
        == 404
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Comment.objects.create(
            org_id=other["id"],
            task_id=item["id"],
            author_id=other["membership"]["id"],
            body="forged",
        )
    with base.restricted(app_role):
        assert Comment.objects.count() == Attachment.objects.count() == 0
        with tenant_transaction(None, other["id"], file_id=row.id):
            assert Attachment.objects.count() == 0
    assert AuditEvent.objects.filter(action="attachment.reserved").count() == 1


@pytest.mark.parametrize("extension,format_name", [(".png", "PNG"), (".jpg", "JPEG")])
def test_image_content_validation(extension, format_name):
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(stream, format=format_name)
    assert validation.validate(io.BytesIO(stream.getvalue()), extension).startswith("image/")
    with pytest.raises(ValueError):
        validation.validate(
            io.BytesIO(stream.getvalue()), ".jpg" if extension == ".png" else ".png"
        )


def test_pdf_and_text_validation():
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(stream)
    assert validation.validate(io.BytesIO(stream.getvalue()), ".pdf") == "application/pdf"
    with pytest.raises(ValueError):
        validation.validate(io.BytesIO(b"%PDF-broken"), ".pdf")
    with pytest.raises(ValueError):
        validation.validate(io.BytesIO(b"\x00binary"), ".txt")


def test_cleanup_operator_retry_and_stale_scan_cannot_resurrect(
    signed_in, app_role, org, item, store
):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    upload(signed_in, app_role, org, item)
    row = Attachment.objects.get()
    route = f"{org['id']}/attachments/{row.id}/"
    assert call(signed_in, app_role, route).json()["attachment"]["state"] == "queued"

    def delete_during_scan(content):
        assert call(signed_in, app_role, route, {}, "DELETE").status_code == 204
        return True

    with patch("collab.validation.scan", side_effect=delete_during_scan):
        process(app_role, row)
    assert row.state == "deleting" and row.cleanup_pending and row.scanned_at is None
    assert call(signed_in, app_role, route).status_code == 404
    with patch("collab.storage.delete", side_effect=OSError("storage unavailable")):
        for _ in range(5):
            process(app_role, row)
    assert row.cleanup_attempts == 5 and row.reserved_bytes == row.size
    with base.restricted(app_role), patch("collab.management.commands.retry_file_cleanup.publish"):
        with pytest.raises(CommandError):
            call_command("retry_file_cleanup", str(uuid.uuid4()), str(row.id), stdout=io.StringIO())
        call_command("retry_file_cleanup", str(row.org_id), str(row.id), stdout=io.StringIO())
    row.refresh_from_db()
    assert row.cleanup_attempts == 0 and row.reserved_bytes == row.size
    process(app_role, row)
    assert row.state == "deleted" and row.reserved_bytes == 0
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 0


def test_multipart_parser_keeps_api_error_envelope():
    client = Client(enforce_csrf_checks=True)
    response = client.post(
        f"/api/v1/orgs/{uuid.uuid4()}/tasks/{uuid.uuid4()}/attachments/",
        {"file": [SimpleUploadedFile("one.txt", b"one"), SimpleUploadedFile("two.txt", b"two")]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "reply, expected",
    [
        (b"stream: OK\0", True),
        (b"stream: test FOUND\0", False),
        (b"stream: OK", None),
        (b"stream: size ERROR\0", None),
    ],
)
def test_scanner_requires_definitive_protocol_reply(reply, expected):
    with patch("collab.validation.socket.create_connection") as connect:
        sock = connect.return_value.__enter__.return_value
        sock.recv.side_effect = [reply, b""]
        if expected is None:
            with pytest.raises(OSError):
                validation.scan(io.BytesIO(b"test"))
        else:
            assert validation.scan(io.BytesIO(b"test")) is expected
        assert sock.sendall.call_args_list[0].args == (b"zINSTREAM\0",)
        assert sock.sendall.call_args_list[-1].args == (b"\0\0\0\0",)
