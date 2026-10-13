import uuid

from django.db import models
from django.utils import timezone


class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    task = models.ForeignKey("projects.Task", on_delete=models.PROTECT)
    author = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    body = models.TextField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="comment_org_id")]
        indexes = [
            models.Index(fields=["org", "task", "created_at", "id"], name="comment_task_order")
        ]


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    task = models.ForeignKey("projects.Task", on_delete=models.PROTECT)
    author = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    filename = models.CharField(max_length=140)
    extension = models.CharField(max_length=5)
    content_type = models.CharField(max_length=40, default="application/octet-stream")
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    object_key = models.CharField(max_length=200, unique=True)
    state = models.CharField(max_length=12, default="uploading")
    reason = models.CharField(max_length=40, default="")
    reserved_bytes = models.PositiveBigIntegerField()
    cleanup_pending = models.BooleanField(default=False)
    scan_attempts = models.PositiveSmallIntegerField(default=0)
    cleanup_attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    scanned_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True)
    request_key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    request_expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="attachment_org_id"),
            models.UniqueConstraint(
                fields=["org", "author", "task", "request_key"], name="attachment_request_key"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "uploading",
                        "queued",
                        "scanning",
                        "available",
                        "rejected",
                        "failed",
                        "deleting",
                        "deleted",
                    ]
                ),
                name="attachment_valid_state",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_bytes=0) | models.Q(reserved_bytes=models.F("size")),
                name="attachment_reservation_size",
            ),
            models.CheckConstraint(
                condition=~models.Q(state="available")
                | models.Q(scanned_at__isnull=False, cleanup_pending=False),
                name="attachment_scanned_before_ready",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "task", "created_at", "id"], name="attachment_task_order"),
            models.Index(fields=["next_attempt_at", "state"], name="attachment_jobs"),
        ]
