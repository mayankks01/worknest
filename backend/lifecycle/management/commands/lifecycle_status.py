import json
from datetime import timedelta
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from lifecycle.models import Maintenance
from lifecycle.tasks import publish
from organizations.context import tenant_transaction
from organizations.models import Organization
from projects.models import Task


class Command(BaseCommand):
    help = "Read a scoped maintenance dry run/receipt; optionally retry an exhausted job."

    def add_arguments(self, parser):
        parser.add_argument("org_id", type=UUID)
        parser.add_argument("job_id", type=UUID)
        parser.add_argument("--retry", action="store_true")

    def handle(self, *args, **options):
        org_id, job_id = options["org_id"], options["job_id"]
        with tenant_transaction(None, org_id, lifecycle_id=job_id):
            row = Maintenance.objects.select_for_update().filter(org_id=org_id, pk=job_id).first()
            if not row:
                raise CommandError("No maintenance job matches this organization and identifier.")
            org = Organization.objects.filter(pk=org_id).first()
            if options["retry"]:
                if row.state != "failed":
                    raise CommandError("Only failed maintenance jobs can be retried.")
                row.state, row.attempts, row.lease = "queued", 0, None
                row.next_attempt_at, row.last_error = timezone.now(), ""
                row.save()
                transaction.on_commit(lambda: publish("maintenance", org_id, job_id))
            self.stdout.write(
                json.dumps(
                    {
                        "organization_id": str(org_id),
                        "job_id": str(job_id),
                        "state": row.state,
                        "deletion_due_at": org.deletion_due_at.isoformat()
                        if org and org.deletion_due_at
                        else None,
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                        "tasks_due_for_purge": Task.objects.filter(
                            org_id=org_id, deleted_at__lte=timezone.now() - timedelta(days=30)
                        ).count(),
                        "attempts": row.attempts,
                        "last_error": row.last_error,
                        "dry_run": not options["retry"],
                    }
                )
            )
