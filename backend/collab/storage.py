"""Private S3 objects; never expose keys or presigned URLs to a client."""

import hashlib
import io

import boto3
from botocore.config import Config
from django.conf import settings
from django.db import connection


def client():
    # Storage calls must never hold business/quota locks.
    if connection.in_atomic_block:
        raise RuntimeError("Storage access inside a database transaction")
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=15,
            retries={"max_attempts": 1},
        ),
    )


def put(key, content):
    client().put_object(
        Bucket=settings.S3_BUCKET, Key=key, Body=content, ContentType="application/octet-stream"
    )


def read(key, expected_size, digest):
    response = client().get_object(Bucket=settings.S3_BUCKET, Key=key)
    stream = response["Body"]
    try:
        data = stream.read(settings.ATTACHMENT_MAX_BYTES + 1)
    finally:
        stream.close()
    if len(data) != expected_size or len(data) > settings.ATTACHMENT_MAX_BYTES:
        raise ValueError("Object size mismatch")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Object integrity mismatch")
    return io.BytesIO(data)


def delete(key):
    client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
