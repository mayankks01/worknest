"""Local integration: real private storage, worker, ClamAV and authenticated downloads."""

import argparse
import json
import secrets
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request
from uuid import uuid4

from verify_accounts import Browser, email_token
from verify_projects import call


def upload(browser, route, name, content, csrf=True, key=None):
    boundary = "worknest-" + uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {
        "Origin": browser.base,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Idempotency-Key": key or uuid4().hex,
    }
    if csrf:
        headers["X-CSRFToken"] = browser.call("session")[1]["csrf_token"]
    request = Request(browser.base + "/api/v1/orgs/" + route, data=body, headers=headers)
    try:
        with browser.opener.open(request, timeout=40) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def wait_file(browser, route, file_id, state, cleanup=False):
    for _ in range(60):
        status, page = call(browser, route)
        assert status == 200, page
        row = next(item for item in page["results"] if item["id"] == file_id)
        if row["state"] == state and (not cleanup or not row["cleanup_pending"]):
            return row
        if row["state"] == "failed":
            raise AssertionError(row)
        time.sleep(2)
    raise AssertionError(f"File did not reach {state}: {row}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--ui-account-file")
    args = parser.parse_args()
    for url in (args.base_url, args.mailpit):
        assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    browser = Browser(args.base_url)
    credentials = {
        "email": f"collab-{uuid4().hex}@example.test",
        "password": "Review-" + secrets.token_urlsafe(24),
    }
    assert (
        browser.call("register", {**credentials, "display_name": "Collaboration Review"})[0] == 202
    )
    assert browser.call("login", credentials)[0] == 200
    token = email_token(args.mailpit, credentials["email"], "Verify your email")
    assert browser.call("verify-email", {"token": token})[0] == 200
    status, data = call(
        browser,
        data={
            "display_name": "TechNova Collaboration Review",
            "slug": "collab-" + uuid4().hex[:12],
            "timezone": "Asia/Kolkata",
        },
        method="POST",
        key=uuid4().hex,
    )
    assert status == 201, data
    org = data["organization"]["id"]
    status, data = call(
        browser, org + "/projects/", {"name": "Website Redesign"}, "POST", uuid4().hex
    )
    assert status == 201, data
    project = data["project"]["id"]
    status, data = call(
        browser,
        f"{org}/projects/{project}/tasks/",
        {
            "title": "Review homepage collaboration",
            "description": "Discuss changes and share reviewed files.",
        },
        "POST",
        uuid4().hex,
    )
    assert status == 201, data
    task = data["task"]["id"]
    route = f"{org}/tasks/{task}/attachments/"
    assert (
        call(
            browser,
            f"{org}/tasks/{task}/comments/",
            {"body": "Ready for review. Please attach the design notes."},
            "POST",
            uuid4().hex,
        )[0]
        == 201
    )
    content = b"WorkNest design notes\nKeep navigation clear and responsive.\n"
    assert upload(browser, route, "design-notes.txt", content, csrf=False)[0] == 403
    key = uuid4().hex
    status, data = upload(browser, route, "design-notes.txt", content, key=key)
    assert status == 202, data
    file_id = data["attachment"]["id"]
    assert (
        upload(browser, route, "design-notes.txt", content, key=key)[1]["attachment"]["id"]
        == file_id
    )
    wait_file(browser, route, file_id, "available")
    url = browser.base + f"/api/v1/orgs/{org}/attachments/{file_id}/download/"
    with browser.opener.open(url, timeout=20) as response:
        assert response.read() == content
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "attachment;" in response.headers["Content-Disposition"]
        assert "no-store" in response.headers["Cache-Control"]
    stranger = Browser(browser.base)
    try:
        stranger.opener.open(url, timeout=10)
        raise AssertionError("Anonymous download succeeded")
    except HTTPError as exc:
        assert exc.code == 401
    # EICAR is the standard harmless antivirus test signature, never executable malware.
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    for name, payload, reason in [
        ("fake.png", b"Not an image", "INVALID_CONTENT"),
        ("scanner-test.txt", eicar, "MALWARE_DETECTED"),
    ]:
        status, data = upload(browser, route, name, payload)
        assert status == 202, data
        rejected = data["attachment"]["id"]
        row = wait_file(browser, route, rejected, "rejected", cleanup=True)
        assert row["reason"] == reason, row
        assert call(browser, f"{org}/attachments/{rejected}/download/")[0] == 409
    assert call(browser, route)[1]["storage"]["used_bytes"] == len(content)
    if args.ui_account_file:
        Path(args.ui_account_file).write_text(
            json.dumps({**credentials, "org_id": org, "project_id": project, "task_id": task}),
            encoding="utf-8",
        )
    print(
        "PASS: real storage/worker/ClamAV clean upload, replay, CSRF, "
        "private download bytes/headers, forged image rejection, EICAR rejection "
        "and exact quota cleanup."
    )


if __name__ == "__main__":
    main()
