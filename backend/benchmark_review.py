"""HTTP benchmark using seed_review's synthetic sessions; never discovers real accounts."""

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 100 or not 1 <= args.rounds <= 100:
        parser.error("Use 1–100 concurrent clients and 1–100 rounds.")
    if urlparse(args.base_url).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Only loopback review servers are allowed.")
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))

    def exercise(user):
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in user["cookies"].items()),
            "X-CSRFToken": user["csrf"],
            "Origin": args.base_url,
            "Content-Type": "application/json",
        }
        root = args.base_url + "/api/v1/orgs/" + user["org_id"] + "/"
        with urlopen(
            Request(root + "tasks/" + user["task_id"] + "/", headers=headers), timeout=90
        ) as response:
            version = json.load(response)["task"]["version"]
        samples = []
        for iteration in range(args.rounds):
            for kind, path, payload in [
                ("read", "search/?q=release", None),
                (
                    "write",
                    "tasks/" + user["task_id"] + "/",
                    {"version": version, "title": f"Bench iteration {iteration + 1}"},
                ),
            ]:
                started = time.perf_counter()
                request = Request(
                    root + path,
                    headers=headers,
                    data=json.dumps(payload).encode() if payload else None,
                    method="PATCH" if payload else "GET",
                )
                try:
                    with urlopen(request, timeout=90) as response:
                        body = json.load(response)
                        status = response.status
                        if kind == "write":
                            version = body["task"]["version"]
                except HTTPError as error:
                    status = error.code
                samples.append((kind, (time.perf_counter() - started) * 1000, status))
        return samples

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        samples = [sample for batch in pool.map(exercise, fixture["users"]) for sample in batch]
    report = {
        "tenants": fixture["tenants"],
        "tasks": fixture["tasks"],
        "concurrency": args.concurrency,
        "rounds": args.rounds,
        "seconds": round(time.perf_counter() - started, 2),
    }
    for kind in ("read", "write"):
        rows = [row for row in samples if row[0] == kind]
        values = sorted(row[1] for row in rows)
        report[kind] = {
            "count": len(rows),
            "errors": sum(row[2] != 200 for row in rows),
            "statuses": {
                str(status): sum(row[2] == status for row in rows)
                for status in sorted({row[2] for row in rows})
            },
            "p50_ms": round(values[math.ceil(len(values) * 0.5) - 1], 2),
            "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1], 2),
        }
    print(json.dumps(report, indent=2))
    if any(report[kind]["errors"] for kind in ("read", "write")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
