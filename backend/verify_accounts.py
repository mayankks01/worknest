"""Local-only HTTP smoke test using Mailpit. Never point this at a public service."""

import argparse
import json
import re
import secrets
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4


class Browser:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, route, data=None, csrf=True):
        headers = {"Origin": self.base}
        if data is not None:
            if csrf:
                _, session = self.call("session")
                headers["X-CSRFToken"] = session["csrf_token"]
            headers["Content-Type"] = "application/json"
        req = Request(
            self.base + "/api/v1/auth/" + route + "/",
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        try:
            with self.opener.open(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)


def email_token(mailpit, email, subject):
    for _ in range(40):
        with urlopen(mailpit + "/api/v1/messages", timeout=5) as response:
            messages = json.load(response)["messages"]
        for message in messages:
            if subject in message["Subject"] and any(
                to["Address"] == email for to in message["To"]
            ):
                with urlopen(mailpit + "/api/v1/message/" + message["ID"], timeout=5) as response:
                    body = json.load(response)["Text"]
                token = re.search(r"#([a-f0-9-]{36}\.[a-f0-9]{64})", body)
                assert token, "Email must contain a fragment-based token"
                return token.group(1)
        time.sleep(1)
    raise AssertionError("Local email was not delivered within 40 seconds")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    args = parser.parse_args()
    for url in [args.base_url, args.mailpit]:
        assert urlparse(url).hostname in {"localhost", "127.0.0.1", "web", "mailpit"}
    email = f"worknest-smoke-{uuid4().hex}@example.test"
    password = "Local-smoke-" + secrets.token_urlsafe(24)
    new_password = "Updated-smoke-" + secrets.token_urlsafe(24)
    first, second = Browser(args.base_url), Browser(args.base_url)
    credentials = {"email": email, "password": password}
    assert first.call("login", credentials, csrf=False)[0] == 403
    assert first.call("register", {**credentials, "display_name": "Local Smoke Test"})[0] == 202
    assert first.call("login", credentials)[1]["user"]["verified_at"] is None
    verify = email_token(args.mailpit, email, "Verify your email")
    assert first.call("verify-email", {"token": verify})[0] == 200
    assert first.call("verify-email", {"token": verify})[0] == 400
    assert first.call("me")[1]["user"]["verified_at"]
    assert second.call("login", credentials)[0] == 200
    assert first.call("password-reset", {"email": email})[0] == 202
    reset = email_token(args.mailpit, email, "Reset your password")
    assert (
        first.call("password-reset/confirm", {"token": reset, "password": new_password})[0] == 200
    )
    assert first.call("me")[0] == second.call("me")[0] == 401
    assert first.call("login", credentials)[0] == 401
    assert first.call("login", {"email": email, "password": new_password})[0] == 200
    assert first.call("logout", {})[0] == 200
    assert first.call("me")[0] == 401
    print(
        "PASS: HTTP CSRF, registration, worker email, verification/replay, reset email, "
        "cross-session invalidation, new-password login, logout."
    )
    print("Created one disposable worknest-smoke account in the local database.")


if __name__ == "__main__":
    main()
