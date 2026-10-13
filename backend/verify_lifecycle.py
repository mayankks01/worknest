"""Local-only Step 9 checks using disposable accounts from verify_activity.py."""

import argparse
import io
import json
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from verify_accounts import Browser
from verify_collaboration import upload, wait_file
from verify_projects import call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--account-file", required=True)
    args = parser.parse_args()
    assert urlparse(args.base_url).hostname in {"localhost", "127.0.0.1"}
    fixture = json.loads(Path(args.account_file).read_text(encoding="utf-8"))
    owner, member = Browser(args.base_url), Browser(args.base_url)
    assert owner.call("login", fixture["owner"])[0] == 200
    assert (
        member.call("login", {"email": fixture["email"], "password": fixture["password"]})[0] == 200
    )
    root = fixture["org_id"] + "/"
    org = call(owner, root)[1]["organization"]
    status, result = call(member, root + "search/?q=notification")
    assert status == 200 and result["count"] >= 1, result
    assert call(member, root + "exports/")[0] == 403
    assert call(member, root + "deletion/")[0] == 403
    route = root + "tasks/" + fixture["task_id"] + "/attachments/"
    status, result = upload(owner, route, "export-review.txt", b"Step 9 private export review")
    assert status == 202, result
    file = result["attachment"]
    wait_file(owner, route, file["id"], "available")
    key = uuid4().hex
    status, result = call(owner, root + "exports/", {}, "POST", key)
    assert status == 202, result
    job = result["export"]
    assert call(owner, root + "exports/", {}, "POST", key)[1]["export"]["id"] == job["id"]
    for _ in range(90):
        job = call(owner, root + "exports/" + job["id"] + "/")[1]["export"]
        if job["state"] == "ready":
            break
        assert job["state"] not in {"failed", "obsolete"}, job
        time.sleep(1)
    assert job["state"] == "ready", job
    with owner.opener.open(args.base_url + job["download_url"], timeout=30) as response:
        assert response.headers["Content-Type"] == "application/zip"
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        manifest = json.loads(bundle.read("workspace.json"))
        assert manifest["organization"]["id"] == fixture["org_id"]
        assert bundle.read("attachments/" + file["id"] + ".txt") == b"Step 9 private export review"
    print(
        "PASS: scoped search, restricted export requests, durable export replay, "
        "real private ZIP and scanned file.",
        flush=True,
    )
    payload = {
        "password": fixture["owner"]["password"],
        "confirmation": org["slug"],
        "action": "schedule",
    }
    scheduled = False
    try:
        status, result = call(owner, root + "deletion/", payload, "POST")
        assert status == 200 and result["state"] == "pending_delete", result
        scheduled = True
        assert call(owner, root + "projects/")[0] == 200
        payload["action"] = "cancel"
        status, result = call(owner, root + "deletion/", payload, "POST")
        assert status == 200 and result["state"] == "active", result
        scheduled = False
    finally:
        if scheduled:
            payload["action"] = "cancel"
            assert call(owner, root + "deletion/", payload, "POST")[0] == 200
    print(
        "PASS: password-confirmed seven-day deletion scheduling, continued access and cancellation."
    )


if __name__ == "__main__":
    main()
