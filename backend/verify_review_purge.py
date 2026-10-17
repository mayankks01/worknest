"""Exercise real object restore/purge only in a synthetic restored review database."""

import hashlib
import os
import sys
from uuid import UUID

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402

from collab import storage  # noqa: E402
from lifecycle.models import Maintenance  # noqa: E402
from lifecycle.tasks import maintain, objects  # noqa: E402
from organizations.context import tenant_transaction  # noqa: E402

if not settings.DEBUG or not connection.settings_dict["NAME"].startswith(
    "worknest_review_restore_"
):
    raise SystemExit("Only isolated restored review databases are allowed")
org, job, other = [str(UUID(value)) for value in sys.argv[1:]]
with tenant_transaction(None, org, lifecycle_id=job):
    if not Maintenance.objects.filter(id=job, org_id=org).exists():
        raise SystemExit("Review maintenance capability does not match")
key, adjacent = f"orgs/{org}/restore-canary.txt", f"orgs/{other}/restore-canary.txt"
data = b"Synthetic WorkNest object restore fixture"
digest = hashlib.sha256(data).hexdigest()
storage.put(key, data)
storage.put(adjacent, data)
try:
    backup = storage.read(key, len(data), digest).read()
    storage.delete(key)
    storage.put(key, backup)
    assert storage.read(key, len(data), digest).read() == data
    maintain(org, job)
    with tenant_transaction(None, org, lifecycle_id=job):
        receipt = Maintenance.objects.get(id=job, org_id=org)
        assert receipt.state == "completed" and receipt.completed_at, receipt.last_error
    assert list(objects(f"orgs/{org}/")) == []
    assert storage.read(adjacent, len(data), digest).read() == data
    print("PASS: synthetic object restore, scoped purge and durable completion receipt")
finally:
    storage.delete(adjacent)
