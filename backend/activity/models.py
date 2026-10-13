import uuid

from django.db import models
from django.utils import timezone


class Preference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    member = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    assignment_email = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "member"], name="preference_member"),
            models.UniqueConstraint(fields=["org", "id"], name="preference_org_id"),
        ]


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    recipient = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    task = models.ForeignKey("projects.Task", on_delete=models.PROTECT)
    source = models.ForeignKey("organizations.OutboxEvent", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True)
    email_state = models.CharField(max_length=10, default="skipped")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease = models.UUIDField(null=True)
    last_error = models.CharField(max_length=40, default="")
    sent_at = models.DateTimeField(null=True)
    message_id = models.CharField(max_length=100, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["org", "recipient", "source", "kind"], name="notification_once"
            ),
            models.UniqueConstraint(fields=["org", "id"], name="notification_org_id"),
            models.CheckConstraint(
                condition=models.Q(kind__in=["assignment", "comment"]), name="notification_kind"
            ),
        ]
        indexes = [
            models.Index(
                fields=["org", "recipient", "created_at", "id"], name="notification_inbox"
            ),
            models.Index(fields=["email_state", "next_attempt_at"], name="notification_jobs"),
        ]


class Reconciliation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.OneToOneField("organizations.Organization", on_delete=models.PROTECT)
    state = models.CharField(max_length=10, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    checked_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=40, default="")
    corrected_counters = models.JSONField(default=list)
    object_cursor = models.TextField(default="")
    orphan_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="reconcile_org_id")]
