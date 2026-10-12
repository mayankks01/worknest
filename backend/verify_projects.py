"""Local-only project/task HTTP smoke through the real restricted runtime role."""

import argparse
import json
import secrets
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request
from uuid import uuid4

from verify_accounts import Browser, email_token
from verify_team import invitation_token, invite_call


def call(browser, path="", data=None, method="GET", key=None, csrf=True):
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
        method=method,
        headers=headers,
    )
    try:
        with browser.opener.open(request, timeout=10) as response:
            return response.status, json.load(response) if response.status != 204 else None
    except HTTPError as error:
        return error.code, json.load(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--ui-account-file")
    args = parser.parse_args()
    for url in (args.base_url, args.mailpit):
        assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
    users, credentials = [], []
    for name in ["Project Owner Review", "Project Member Review"]:
        login = {
            "email": f"project-{uuid4().hex}@example.test",
            "password": "Project-review-" + secrets.token_urlsafe(24),
        }
        browser = Browser(args.base_url)
        assert browser.call("register", {**login, "display_name": name})[0] == 202
        assert browser.call("login", login)[0] == 200
        token = email_token(args.mailpit, login["email"], "Verify your email")
        assert browser.call("verify-email", {"token": token})[0] == 200
        users.append(browser)
        credentials.append(login)
    owner, member = users
    result = call(
        owner,
        data={
            "display_name": "TechNova Project Review",
            "slug": "projects-" + uuid4().hex[:12],
            "timezone": "Asia/Kolkata",
        },
        method="POST",
        key=uuid4().hex,
    )
    assert result[0] == 201, result
    org = result[1]["organization"]
    root = org["id"] + "/"
    invited = call(
        owner,
        root + "invitations/",
        {"email": credentials[1]["email"], "role": "Member"},
        "POST",
        uuid4().hex,
    )
    assert invited[0] == 201, invited
    token = invitation_token(args.mailpit, credentials[1]["email"], invited[1]["invitation"]["id"])
    assert invite_call(member, "accept", token)[0] == 200
    roster = call(owner, root + "members/")[1]["results"]
    target = next(m for m in roster if m["email"] == credentials[1]["email"])
    payload = {"name": "Website Redesign", "description": "A clearer home for our next chapter."}
    key = uuid4().hex
    created = call(owner, root + "projects/", payload, "POST", key)
    assert created[0] == 201, created
    project = created[1]["project"]
    assert call(owner, root + "projects/", payload, "POST", key) == created
    route = root + "projects/" + project["id"] + "/"
    assert call(member, route)[0] == 404
    assert call(member, root + "projects/")[1]["results"] == []
    assert call(owner, route + "members/", {"membership_id": target["id"]}, "POST")[0] == 200
    assert call(member, route)[0] == 200
    assert call(member, route + "archive/", {}, "POST")[0] == 403
    payload = {
        "title": "Design homepage layout",
        "description": "Review the navigation and hero.",
        "assignee_id": target["id"],
        "priority": "High",
        "due_date": "2026-10-11",
    }
    assert call(member, route + "tasks/", payload, "POST", uuid4().hex, csrf=False)[0] == 403
    key = uuid4().hex
    created = call(member, route + "tasks/", payload, "POST", key)
    assert created[0] == 201, created
    task = created[1]["task"]
    assert call(member, route + "tasks/", payload, "POST", key) == created
    task_route = root + "tasks/" + task["id"] + "/"
    assert call(member, task_route, {"status": "In Progress", "version": 1}, "PATCH")[0] == 200
    stale = call(owner, task_route, {"title": "Stale draft", "version": 1}, "PATCH")
    assert stale[0] == 409 and stale[1]["error"]["details"]["current_version"] == 2, stale
    assert call(member, task_route, {"version": 2}, "DELETE")[0] == 403
    assert call(owner, route + "archive/", {}, "POST")[0] == 200
    assert call(member, task_route, {"status": "Done", "version": 2}, "PATCH")[0] == 409
    assert call(owner, route + "restore/", {}, "POST")[0] == 200
    assert call(owner, route + "members/" + target["id"] + "/", {}, "DELETE")[0] == 204
    assert call(member, task_route)[0] == 404
    cleared = call(owner, task_route)[1]["task"]
    assert cleared["assignee_id"] is None and cleared["version"] == 3
    assert call(owner, route + "members/", {"membership_id": target["id"]}, "POST")[0] == 200
    # Persist disposable review records for the board/list browser check.
    for title, status, priority in [
        ("Map the navigation", "To Do", "Medium"),
        ("Review responsive components", "In Progress", "High"),
        ("Confirm brand assets", "Blocked", "Urgent"),
        ("Set up the project workspace", "Done", "Low"),
    ]:
        result = call(
            owner,
            route + "tasks/",
            {
                "title": title,
                "status": status,
                "priority": priority,
                "assignee_id": org["membership"]["id"],
            },
            "POST",
            uuid4().hex,
        )
        assert result[0] == 201, result
    temporary = call(
        owner, route + "tasks/", {"title": "Disposable deletion check"}, "POST", uuid4().hex
    )[1]["task"]
    temporary_path = root + "tasks/" + temporary["id"] + "/"
    assert call(owner, temporary_path, {"version": 1}, "DELETE")[0] == 204
    assert call(owner, temporary_path)[0] == 404
    assert call(owner, root + "dashboard/")[1]["counts"]["projects"] == 1
    if args.ui_account_file:
        Path(args.ui_account_file).write_text(
            json.dumps(
                {
                    **credentials[0],
                    "org_id": org["id"],
                    "project_id": project["id"],
                    "task_id": task["id"],
                    "member": credentials[1],
                }
            ),
            encoding="utf-8",
        )
    print(
        "PASS: runtime project privacy, membership, task CRUD, replay, CSRF, 409 conflicts, "
        "archive/restore, offboarding, dashboard and versioned deletion."
    )


if __name__ == "__main__":
    main()
