"""Local HTTP organization smoke, using the actual app role and browser proxy."""

import argparse
import json
import secrets
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request
from uuid import uuid4

from verify_accounts import Browser, email_token


def org_call(browser, path="", data=None, method="GET", key=None, csrf=True):
    headers = {"Origin": browser.base}
    if data is not None:
        headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRFToken"] = browser.call("session")[1]["csrf_token"]
    if key:
        headers["Idempotency-Key"] = key
    request = Request(
        browser.base + "/api/v1/orgs/" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with browser.opener.open(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument(
        "--ui-account-file", help="Optional scratch file outside Git for browser QA"
    )
    args = parser.parse_args()
    for url in (args.base_url, args.mailpit):
        assert urlparse(url).hostname in {"localhost", "127.0.0.1", "web", "mailpit"}
    browsers = []
    credentials = []
    for _ in range(2):
        browser = Browser(args.base_url)
        login = {
            "email": f"worknest-org-smoke-{uuid4().hex}@example.test",
            "password": "Organization-review-" + secrets.token_urlsafe(24),
        }
        assert browser.call("register", {**login, "display_name": "Workspace Review"})[0] == 202
        assert browser.call("login", login)[0] == 200
        assert (
            org_call(
                browser,
                data={"display_name": "Blocked", "slug": "blocked"},
                method="POST",
                key=uuid4().hex,
            )[0]
            == 403
        )
        token = email_token(args.mailpit, login["email"], "Verify your email")
        assert browser.call("verify-email", {"token": token})[0] == 200
        browsers.append(browser)
        credentials.append(login)
    first, other = browsers
    assert org_call(first)[1]["results"] == []
    payload = {
        "display_name": "TechNova Review",
        "slug": f"technova-{uuid4().hex[:12]}",
        "timezone": "Asia/Kolkata",
    }
    key = uuid4().hex
    assert org_call(first, data=payload, method="POST", key=key, csrf=False)[0] == 403
    response = org_call(first, data=payload, method="POST", key=key)
    assert response[0] == 201, response
    assert org_call(first, data=payload, method="POST", key=key) == response
    assert (
        org_call(first, data={**payload, "display_name": "Conflict"}, method="POST", key=key)[0]
        == 409
    )
    org = response[1]["organization"]
    assert org_call(other, path=f"{org['id']}/")[0] == 404
    assert org_call(other)[1]["results"] == []
    assert (
        org_call(other, path=f"{org['id']}/", method="PATCH", data={"display_name": "Stolen"})[0]
        == 404
    )
    assert (
        org_call(first, path=f"{org['id']}/", method="PATCH", data={"timezone": "Europe/London"})[0]
        == 200
    )
    assert (
        org_call(first, path=f"{org['id']}/", method="PATCH", data={"timezone": "Asia/Kolkata"})[0]
        == 200
    )
    second = org_call(
        first,
        data={"display_name": "Bright Labs Review", "slug": f"bright-{uuid4().hex[:12]}"},
        method="POST",
        key=uuid4().hex,
    )
    assert second[0] == 201
    assert len(org_call(first)[1]["results"]) == 2
    assert (
        org_call(
            first,
            path=f"{org['id']}/",
            method="PATCH",
            data={"org_id": second[1]["organization"]["id"]},
        )[0]
        == 400
    )
    if args.ui_account_file:
        Path(args.ui_account_file).write_text(json.dumps(credentials[0]), encoding="utf-8")
    print(
        "PASS: real SMTP verification, CSRF, creation, idempotent replay/conflict, "
        "settings, discovery, cross-tenant denial and body-field rejection."
    )
    print(
        "Created two disposable local review accounts and two organizations; "
        "no sample data added to other accounts."
    )


if __name__ == "__main__":
    main()
