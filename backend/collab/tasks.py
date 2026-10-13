import secrets
import uuid
from datetime import timedelta

from celery import shared_task
from django.db import connection
from django.utils import timezone

from organizations.context import tenant_transaction
from organizations.models import OrganizationUsage

from . import storage, validation
from .models import Attachment
from .services import authorized_upload, file_event


def publish(org_id, file_id):
    try:
        process_attachment.apply_async(args=[str(org_id), str(file_id)], retry=False)
    except Exception:
        pass  # Durable file job state is recovered by beat.


@shared_task(name="collab.dispatch_files")
def dispatch_files():
    with connection.cursor() as cursor:
        cursor.execute("SELECT org_id, file_id FROM public.worknest_pending_file_jobs()")
        routes = cursor.fetchall()
    for org_id, file_id in routes:
        publish(org_id, file_id)


@shared_task(name="collab.process_attachment", soft_time_limit=100, time_limit=120)
def process_attachment(org_id, file_id):
    with tenant_transaction(None, org_id, file_id=file_id):
        usage = OrganizationUsage.objects.select_for_update().filter(org_id=org_id).first()
        item = Attachment.objects.select_for_update().filter(org_id=org_id, pk=file_id).first()
        now = timezone.now()
        if not usage or not item or item.next_attempt_at > now:
            return
        if item.state == "uploading":
            item.state, item.reason, item.cleanup_pending = "failed", "UPLOAD_EXPIRED", True
        cleanup = item.cleanup_pending
        if cleanup and item.cleanup_attempts >= 5:
            return  # Failed cleanup is durable, retains quota, and needs an explicit retry.
        if not cleanup and item.state not in {"queued", "scanning"}:
            return
        if not cleanup and (item.scan_attempts >= 5 or not authorized_upload(item)):
            item.state, item.reason, item.cleanup_pending = "failed", "ACCESS_OR_RETRY_LIMIT", True
            cleanup = True
        if cleanup:
            item.cleanup_attempts += 1
        else:
            item.scan_attempts += 1
            item.state = "scanning"
        lease = uuid.uuid4()
        item.lease = lease
        item.next_attempt_at = now + timedelta(minutes=5)
        item.save()
        key, size, digest, extension = item.object_key, item.size, item.sha256, item.extension
    # All object/scanner I/O happens outside database transactions and quota locks.
    outcome, mime = "available", ""
    try:
        if cleanup:
            storage.delete(key)
        else:
            content = storage.read(key, size, digest)
            try:
                if not validation.scan(content):
                    outcome = "MALWARE_DETECTED"
                else:
                    mime = validation.validate(content, extension)
            finally:
                content.close()
    except ValueError:
        outcome = "INVALID_CONTENT"
    except Exception:
        outcome = "SERVICE_UNAVAILABLE"
    with tenant_transaction(None, org_id, file_id=file_id):
        usage = OrganizationUsage.objects.select_for_update().get(org_id=org_id)
        item = Attachment.objects.select_for_update().get(org_id=org_id, pk=file_id)
        if item.lease != lease:
            return  # A deletion/new claim superseded this worker; never resurrect its result.
        item.lease = None
        if cleanup:
            if outcome == "available":
                usage.storage_bytes -= item.reserved_bytes
                usage.save(update_fields=["storage_bytes"])
                item.reserved_bytes, item.cleanup_pending = 0, False
                if item.state == "deleting":
                    item.state = "deleted"
            else:
                item.reason = "CLEANUP_UNAVAILABLE"
        elif outcome == "SERVICE_UNAVAILABLE" and item.scan_attempts < 5:
            item.state, item.reason = "queued", outcome
        elif outcome == "available" and authorized_upload(item):
            item.state, item.reason, item.content_type = "available", "", mime
            item.scanned_at = timezone.now()
        else:
            item.state = "failed" if outcome == "SERVICE_UNAVAILABLE" else "rejected"
            item.reason = "ACCESS_REVOKED" if outcome == "available" else outcome
            item.cleanup_pending = True
        attempts = item.cleanup_attempts if cleanup else item.scan_attempts
        item.next_attempt_at = timezone.now() + timedelta(
            seconds=10 * 2**attempts + secrets.randbelow(10)
        )
        if item.cleanup_pending and not cleanup:
            item.next_attempt_at = timezone.now()
        item.save()
        file_event(
            item,
            "attachment.cleaned"
            if cleanup and not item.cleanup_pending
            else "attachment.processed",
        )
