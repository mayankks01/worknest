"""Initialize the local S3 bucket; fail if its configuration grants public access."""

import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def storage_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 3},
        ),
    )


def initialize():
    client = storage_client()
    bucket = os.environ["S3_BUCKET"]
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise

    # A fresh S3 bucket is private by default. Refuse unexpected policies rather
    # than silently overwriting a policy on a reused volume.
    try:
        client.get_bucket_policy(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
            raise
    else:
        raise RuntimeError("Local bucket has an unexpected policy; review it before proceeding.")
    print("Private local bucket initialized; no bucket policy grants public access.")


if __name__ == "__main__":
    initialize()
