"""Local HTTP and worker-outage checks for inbox, email, preferences and audit."""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

from verify_accounts import Browser, email_token
from verify_projects import call
from verify_team import invitation_token, invite_call


def wait_inbox(browser, root, count):
    for _ in range(60):
        status, data = call(browser, root + "notifications/")
        assert status == 200, data
        if len(data["results"]) == count:
            return data
        time.sleep(1)
    raise AssertionError("Inbox did not reach expected count")


def assignment_messages(mailpit, address):
    with urlopen(mailpit + "/api/v1/messages", timeout=5) as response:
        return [
            message
            for message in json.load(response)["messages"]
            if message["Subject"] == "You have a new task assignment in WorkNest"
            and any(recipient["Address"] == address for recipient in message["To"])
        ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument(
        "--pause-worker",
        action="store_true",
        help="Temporarily stop/restart the local Compose worker",
    )
    parser.add_argument("--ui-account-file")
    args = parser.parse_args()
    for url in (args.base_url, args.mailpit):
        assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
    users, logins = [], []
    for name in ["Activity Owner Review", "Activity Member Review"]:
        credentials = {
            "email": f"activity-{uuid4().hex}@example.test",
            "password": "Local-review-" + secrets.token_urlsafe(24),
        }
        browser = Browser(args.base_url)
        assert browser.call("register", {**credentials, "display_name": name})[0] == 202
        assert browser.call("login", credentials)[0] == 200
        token = email_token(args.mailpit, credentials["email"], "Verify your email")
        assert browser.call("verify-email", {"token": token})[0] == 200
        users.append(browser)
        logins.append(credentials)
    owner, member = users
    status, data = call(
        owner,
        data={
            "display_name": "TechNova Activity Review",
            "slug": "activity-" + uuid4().hex[:12],
            "timezone": "Asia/Kolkata",
        },
        method="POST",
        key=uuid4().hex,
    )
    assert status == 201, data
    org = data["organization"]
    root = org["id"] + "/"
    invited = call(
        owner,
        root + "invitations/",
        {"email": logins[1]["email"], "role": "Member"},
        "POST",
        uuid4().hex,
    )
    assert invited[0] == 201, invited
    token = invitation_token(args.mailpit, logins[1]["email"], invited[1]["invitation"]["id"])
    assert invite_call(member, "accept", token)[0] == 200
    roster = call(owner, root + "members/")[1]["results"]
    target = next(row for row in roster if row["email"] == logins[1]["email"])
    status, data = call(
        owner, root + "projects/", {"name": "Website Redesign"}, "POST", uuid4().hex
    )
    assert status == 201, data
    project = data["project"]
    path = root + "projects/" + project["id"] + "/"
    assert call(owner, path + "members/", {"membership_id": target["id"]}, "POST")[0] == 200
    key = uuid4().hex
    payload = {
        "title": "Review the notification experience",
        "assignee_id": target["id"],
        "priority": "High",
    }
    try:
        if args.pause_worker:
            subprocess.run(["docker", "compose", "stop", "worker"], check=True, capture_output=True)
        status, data = call(owner, path + "tasks/", payload, "POST", key)
        assert status == 201, data
        task = data["task"]
        assert call(owner, path + "tasks/", payload, "POST", key)[1] == data
        assert call(member, root + "tasks/" + task["id"] + "/")[0] == 200
        if args.pause_worker:
            assert call(member, root + "notifications/")[1]["results"] == []
            assert assignment_messages(args.mailpit, logins[1]["email"]) == []
            assert call(owner, root + "operations/")[1]["pending_events"] > 0
            print("PASS: committed task and durable event survive a stopped worker.", flush=True)
    finally:
        if args.pause_worker:
            subprocess.run(
                ["docker", "compose", "start", "worker"], check=True, capture_output=True
            )
    inbox = wait_inbox(member, root, 1)
    assert inbox["results"][0]["kind"] == "assignment" and inbox["unread_count"] == 1
    for _ in range(60):
        messages = assignment_messages(args.mailpit, logins[1]["email"])
        if messages:
            break
        time.sleep(1)
    assert len(messages) == 1
    with urlopen(args.mailpit + "/api/v1/message/" + messages[0]["ID"], timeout=5) as response:
        body = json.load(response)["Text"]
        assert "?task=" + task["id"] in body and task["title"] not in body
    notice = inbox["results"][0]["id"]
    assert call(member, root + "notifications/" + notice + "/", {"read": True}, "PATCH")[0] == 200
    assert call(member, root + "notifications/?unread=true")[1]["unread_count"] == 0
    assert (
        call(
            owner,
            root + "tasks/" + task["id"] + "/comments/",
            {"body": "Please check the layout and keyboard navigation."},
            "POST",
            uuid4().hex,
        )[0]
        == 201
    )
    assert len(wait_inbox(member, root, 2)["results"]) == 2
    assert (
        call(member, root + "notifications/preferences/", {"assignment_email": False}, "PATCH")[0]
        == 200
    )
    assert (
        call(
            owner,
            path + "tasks/",
            {"title": "Confirm the email preference", "assignee_id": target["id"]},
            "POST",
            uuid4().hex,
        )[0]
        == 201
    )
    wait_inbox(member, root, 3)
    time.sleep(2)
    assert len(assignment_messages(args.mailpit, logins[1]["email"])) == 1
    assert call(member, root + "audit/")[0] == 403 and call(member, root + "operations/")[0] == 403
    assert call(owner, root + "audit/")[0] == 200
    for _ in range(60):
        operations = call(owner, root + "operations/")[1]
        if operations["reconciliation"]["checked_at"]:
            break
        time.sleep(1)
    assert operations["reconciliation"]["checked_at"]
    if args.ui_account_file:
        Path(args.ui_account_file).write_text(
            json.dumps(
                {
                    **logins[1],
                    "owner": logins[0],
                    "org_id": org["id"],
                    "project_id": project["id"],
                    "task_id": task["id"],
                }
            ),
            encoding="utf-8",
        )
    print(
        "PASS: recovered assignment inbox/email, replay without duplicate inbox, comment alert, "
        "read state, per-organization preference, restricted audit/operations "
        "and scheduled reconciliation."
    )


if __name__ == "__main__":
    main()
