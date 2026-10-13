"""Retry a specific exhausted cleanup after the storage service is repaired."""

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from collab.models import Attachment
from collab.services import file_event
from collab.tasks import publish
from organizations.context import tenant_transaction
from organizations.models import OrganizationUsage


class Command(BaseCommand):
    help = "Retry cleanup for one organization/file pair; never release quota manually."

    def add_arguments(self, parser):
        parser.add_argument("org_id", type=UUID)
        parser.add_argument("file_id", type=UUID)

    def handle(self, org_id, file_id, **options):
        with tenant_transaction(None, org_id, file_id=file_id):
            OrganizationUsage.objects.select_for_update().filter(org_id=org_id).first()
            item = Attachment.objects.select_for_update().filter(org_id=org_id, pk=file_id).first()
            if not item or not item.cleanup_pending or item.cleanup_attempts < 5:
                raise CommandError("No exhausted cleanup exists for this organization/file pair.")
            item.cleanup_attempts = 0
            item.next_attempt_at = timezone.now()
            item.lease = None
            item.save(update_fields=["cleanup_attempts", "next_attempt_at", "lease"])
            file_event(item, "attachment.cleanup_retried")
            transaction.on_commit(lambda: publish(org_id, file_id))
        self.stdout.write(
            self.style.SUCCESS(
                "Cleanup queued; reserved storage remains held until deletion succeeds."
            )
        )
