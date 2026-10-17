"""Synthetic fixtures only; refuses existing/non-review databases."""

import json
import os
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.utils import timezone

from accounts.models import User
from organizations.context import tenant_transaction
from organizations.models import OrganizationUsage
from projects.models import Task


class Command(BaseCommand):
    help = "Seed unrelated synthetic organizations in an empty worknest_review_* database."

    def add_arguments(self, parser):
        parser.add_argument("--tenants", type=int, default=2)
        parser.add_argument("--tasks", type=int, default=8)

    def handle(self, *args, **options):
        if not settings.DEBUG or not connection.settings_dict["NAME"].startswith(
            "worknest_review_"
        ):
            raise CommandError("Only DEBUG worknest_review_* databases are allowed.")
        if User.objects.exists():
            raise CommandError("Review database must be empty; existing data is never overwritten.")
        tenants, count = options["tenants"], options["tasks"]
        if not 2 <= tenants <= 100 or not 1 <= count <= 10000:
            raise CommandError("Use 2–100 tenants and 1–10000 tasks per tenant.")
        fixtures = []
        for index in range(tenants):
            user = User.objects.create_user(
                email=f"owner-{index + 1}@example.test",
                password=os.environ.get("REVIEW_PASSWORD"),
                display_name=f"Demo Owner {index + 1}",
                verified_at=timezone.now(),
            )
            client = Client(HTTP_HOST="localhost")
            client.force_login(user)
            response = client.post(
                "/api/v1/orgs/",
                data=json.dumps(
                    {"display_name": f"Demo Team {index + 1}", "slug": f"demo-{index + 1}"}
                ),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=uuid4().hex,
            )
            if response.status_code != 201:
                raise CommandError(f"Organization seed failed: {response.status_code}")
            org = response.json()["organization"]
            response = client.post(
                f"/api/v1/orgs/{org['id']}/projects/",
                data=json.dumps(
                    {"name": "Website Redesign", "description": "Synthetic release fixture"}
                ),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=uuid4().hex,
            )
            if response.status_code != 201:
                raise CommandError(f"Project seed failed: {response.status_code}")
            project = response.json()["project"]
            with tenant_transaction(user.id, org["id"]):
                tasks = Task.objects.bulk_create(
                    [
                        Task(
                            org_id=org["id"],
                            project_id=project["id"],
                            title=f"Review release task {number + 1}",
                            description="Synthetic search and benchmark content.",
                            created_by_id=org["membership"]["id"],
                        )
                        for number in range(count)
                    ],
                    batch_size=1000,
                )
                OrganizationUsage.objects.filter(org_id=org["id"]).update(active_tasks=count)
            csrf = client.get("/api/v1/auth/session/").json()["csrf_token"]
            fixtures.append(
                {
                    "org_id": org["id"],
                    "project_id": project["id"],
                    "task_id": str(tasks[0].id),
                    "email": user.email,
                    "cookies": {key: value.value for key, value in client.cookies.items()},
                    "csrf": csrf,
                }
            )
        # Contains disposable session credentials: redirect outside Git and remove after review.
        self.stdout.write(
            json.dumps({"tenants": tenants, "tasks": tenants * count, "users": fixtures})
        )
