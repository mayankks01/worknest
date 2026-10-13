import uuid

from django.db import models
from django.utils import timezone


class Export(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    requester = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    state = models.CharField(max_length=12, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True)
    object_key = models.CharField(max_length=200, default="")
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, default="")
    last_error = models.CharField(max_length=40, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="export_org_id")]


class Maintenance(models.Model):
    # Non-identifying completion receipt survives deletion of the organization.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField(unique=True)
    state = models.CharField(max_length=12, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    checked_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=40, default="")
