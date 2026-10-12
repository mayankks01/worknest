"""Local-only Step 5 HTTP/SMTP smoke using the real restricted runtime role."""

import argparse
import json
import re
import secrets
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from verify_accounts import Browser, email_token
from verify_organizations import org_call


def invitation_token(mailpit, email, invitation_id=None):
    for _ in range(45):
        with urlopen(mailpit + "/api/v1/messages", timeout=5) as response:
            messages = json.load(response)["messages"]
        for message in messages:
            if "invited" in message["Subject"] and any(
                x["Address"] == email for x in message["To"]
            ):
                with urlopen(mailpit + "/api/v1/message/" + message["ID"], timeout=5) as response:
                    body = json.load(response)["Text"]
                match = re.search(r"#([a-f0-9-]{36}\.[a-f0-9-]{36}\.[a-f0-9]{64})", body)
                assert match, "Missing fragment invitation link"
                if invitation_id is None or match[1].split(".")[1] == invitation_id:
                    return match[1]
        time.sleep(1)
    raise AssertionError("Invitation not delivered to local Mailpit within 45 seconds")


def invite_call(browser, action, token, csrf=True):
    headers = {"Origin": browser.base, "Content-Type": "application/json"}
    if csrf:
        headers["X-CSRFToken"] = browser.call("session")[1]["csrf_token"]
    request = Request(
        browser.base + "/api/v1/invitations/" + action + "/",
        data=json.dumps({"token": token}).encode(),
        headers=headers,
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
    parser.add_argument("--ui-account-file")
    args = parser.parse_args()
    for url in (args.base_url, args.mailpit):
        assert urlparse(url).hostname in {"localhost", "127.0.0.1"}
    browsers, credentials = [], []
    for name in ["Team Owner Review", "Team Member Review"]:
        login = {
            "email": f"team-{uuid4().hex}@example.test",
            "password": "Team-review-" + secrets.token_urlsafe(24),
        }
        browser = Browser(args.base_url)
        assert browser.call("register", {**login, "display_name": name})[0] == 202
        assert browser.call("login", login)[0] == 200
        token = email_token(args.mailpit, login["email"], "Verify your email")
        assert browser.call("verify-email", {"token": token})[0] == 200
        browsers.append(browser)
        credentials.append(login)
    owner, member = browsers
    result = org_call(
        owner,
        data={"display_name": "TechNova Team Review", "slug": "team-" + uuid4().hex[:12]},
        method="POST",
        key=uuid4().hex,
    )
    assert result[0] == 201, result
    org = result[1]["organization"]
    root = org["id"] + "/"
    invitation = org_call(
        owner,
        root + "invitations/",
        {"email": credentials[1]["email"], "role": "Member"},
        "POST",
        uuid4().hex,
    )
    assert invitation[0] == 201, invitation
    token = invitation_token(
        args.mailpit, credentials[1]["email"], invitation[1]["invitation"]["id"]
    )
    assert invite_call(member, "preview", token)[0] == 200
    assert invite_call(member, "accept", token, csrf=False)[0] == 403
    accepted = invite_call(member, "accept", token)
    assert accepted[0] == 200, accepted
    assert invite_call(member, "accept", token)[0] == 400
    roster = org_call(owner, root + "members/")[1]
    target = next(m for m in roster["results"] if m["email"] == credentials[1]["email"])
    assert (
        org_call(owner, root + "members/" + target["id"] + "/", {"role": "Admin"}, "PATCH")[0]
        == 200
    )
    transfer = org_call(
        owner,
        root + "transfer-ownership/",
        {"member_id": target["id"], "password": credentials[0]["password"]},
        "POST",
    )
    assert transfer[0] == 200, transfer
    assert org_call(owner, root + "members/")[1]["role"] == "Admin"
    assert org_call(member, root + "members/")[1]["role"] == "Owner"
    removed = org_call(member, root + "members/" + org["membership"]["id"] + "/", {}, "DELETE")
    assert removed[0] == 200, removed
    assert org_call(owner, root + "members/")[0] == 404
    invitation = org_call(
        member,
        root + "invitations/",
        {"email": credentials[0]["email"], "role": "Member"},
        "POST",
        uuid4().hex,
    )
    assert invitation[0] == 201
    token = invitation_token(
        args.mailpit, credentials[0]["email"], invitation[1]["invitation"]["id"]
    )
    assert invite_call(owner, "accept", token)[0] == 200
    if args.ui_account_file:
        Path(args.ui_account_file).write_text(
            json.dumps({**credentials[1], "org_id": org["id"]}), encoding="utf-8"
        )
    print(
        "PASS: SMTP invitation, preview, CSRF, accept/replay, role edit, "
        "transfer, removal and rejoin using actual app role."
    )


if __name__ == "__main__":
    main()
