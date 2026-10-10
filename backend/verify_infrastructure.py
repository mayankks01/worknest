"""Local/CI integration probe. Creates then removes its own temporary S3 object."""

import json
import os
import smtplib
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

import django

from initialize_storage import storage_client

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402

from config.celery import app  # noqa: E402


def verify():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname=current_user"
        )
        assert cursor.fetchone() == (False, False, False, False), "Unsafe application role"
        cursor.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user"
        )
        assert cursor.fetchone()[0] == 0, "Application role must not own tables"
    print("PASS: restricted application database role")

    with urlopen("http://web:8000/api/v1/health/ready/", timeout=10) as response:
        assert json.load(response)["status"] == "ready"
    print("PASS: live PostgreSQL and Redis readiness")

    assert app.control.inspect(timeout=10).ping(), "No Celery worker responded"
    print("PASS: worker broker round trip")

    with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=5) as smtp:
        assert smtp.noop()[0] == 250
    print("PASS: local SMTP sandbox connection (no email sent)")

    client = storage_client()
    bucket = os.environ["S3_BUCKET"]
    key = f"foundation-probe/{uuid4()}.txt"
    content = b"WorkNest infrastructure probe"
    client.put_object(Bucket=bucket, Key=key, Body=content, ContentType="text/plain")
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"]
        try:
            assert body.read() == content
        finally:
            body.close()
        anonymous_url = f"{os.environ['S3_ENDPOINT_URL'].rstrip('/')}/{bucket}/{key}"
        try:
            with urlopen(anonymous_url, timeout=5):
                raise AssertionError("Anonymous object access unexpectedly succeeded")
        except HTTPError as error:
            assert error.code == 403, f"Expected anonymous access denial, got {error.code}"
        print("PASS: signed object read works; anonymous read is denied")
    finally:
        client.delete_object(Bucket=bucket, Key=key)


if __name__ == "__main__":
    verify()
