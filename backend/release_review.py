"""Run only against local Compose; creates new synthetic databases, never drops databases."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent


def run(*args, input=None):
    result = subprocess.run(
        args, cwd=ROOT, input=input, text=True, capture_output=True, check=False
    )
    if result.returncode:
        # Commands contain no credentials; avoid dumping potentially sensitive process output.
        raise RuntimeError(
            f"Review command failed ({result.returncode}): {' '.join(args[:5])}\n"
            + result.stderr[-1500:]
        )
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tenants", type=int, default=2)
    parser.add_argument("--tasks", type=int, default=8)
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("Write disposable sessions and review results outside the repository.")
    output.mkdir(parents=True, exist_ok=False)
    suffix = uuid4().hex[:12]
    source, restored = f"worknest_review_{suffix}", f"worknest_review_restore_{suffix}"
    compose = ["docker", "compose"]
    db = [*compose, "exec", "-T", "db"]

    def sql(database, query):
        return run(
            *db,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            "-c",
            query,
        ).strip()

    # Read configured role identifiers without logging credentials or evaluating .env as code.
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    owner, app = values["DATABASE_OWNER"], values["DATABASE_USER"]
    if not all(value.replace("_", "").isalnum() for value in [owner, app]):
        parser.error("Review requires simple configured database role names.")

    def application(database, service, *command):
        return run(
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-v",
            f"{ROOT / 'backend'}:/app:ro",
            "-e",
            f"POSTGRES_DB={database}",
            "-e",
            "CELERY_BROKER_URL=redis://redis:6379/15",
            service,
            *command,
        )

    run(*db, "createdb", "-U", "postgres", "-O", owner, source)
    sql(
        source,
        f'ALTER SCHEMA public OWNER TO "{owner}"; REVOKE CREATE ON SCHEMA public FROM PUBLIC; '
        f'GRANT USAGE ON SCHEMA public TO "{app}"; '
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app}"; '
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{app}";',
    )
    print("Created isolated review database; applying migrations.", flush=True)
    application(source, "migrate", "python", "manage.py", "migrate", "--noinput")
    print("Seeding synthetic organizations and tasks.", flush=True)
    fixture = application(
        source,
        "web",
        "python",
        "manage.py",
        "seed_review",
        "--tenants",
        str(args.tenants),
        "--tasks",
        str(args.tasks),
    )
    json.loads(fixture)
    fixture_path = output / "sessions.json"
    fixture_path.write_text(fixture, encoding="utf-8")
    query = (
        "SELECT count(*), md5(string_agg(id::text || title || version::text, ',' ORDER BY id)) "
        "FROM projects_task"
    )
    before = sql(source, query)
    dump = f"/tmp/{source}.dump"
    run(*db, "pg_dump", "-U", "postgres", "-Fc", "-f", dump, source)
    run(*db, "createdb", "-U", "postgres", "-O", owner, restored)
    started = time.perf_counter()
    run(*db, "pg_restore", "-U", "postgres", "--exit-on-error", "-d", restored, dump)
    elapsed = time.perf_counter() - started
    after = sql(restored, query)
    if before != after:
        raise RuntimeError("Restored task content differs from source")
    if sql(restored, "SELECT count(*) FROM pg_constraint WHERE NOT convalidated") != "0":
        raise RuntimeError("Restored database contains unvalidated constraints")
    application(restored, "web", "python", "verify_tenant_security.py")
    application(restored, "migrate", "python", "manage.py", "migrate", "--check")
    users = json.loads(fixture)["users"]
    org, other = users[0]["org_id"], users[1]["org_id"]
    job = sql(restored, f"SELECT id FROM lifecycle_maintenance WHERE org_id='{org}'")
    # The time advance is restricted to this newly created synthetic restore database.
    sql(
        restored,
        "UPDATE organizations_organization SET state='pending_delete', "
        f"deletion_due_at=now()-interval '1 hour' WHERE id='{org}'",
    )
    application(restored, "web", "python", "verify_review_purge.py", org, job, other)
    if sql(restored, f"SELECT count(*) FROM organizations_organization WHERE id='{org}'") != "0":
        raise RuntimeError("Restored tenant purge did not finish")
    report = {
        "source_database": source,
        "restored_database": restored,
        "tenants": args.tenants,
        "tasks": args.tenants * args.tasks,
        "restore_seconds": round(elapsed, 3),
        "task_content_matches": True,
        "validated_constraints": True,
        "restricted_role_rls": True,
        "object_restore_and_scoped_purge": True,
        "fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
    }
    (output / "restore.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if args.benchmark:
        name = f"worknest-review-{suffix}"
        try:
            run(
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-d",
                "--name",
                name,
                "-v",
                f"{ROOT / 'backend'}:/app:ro",
                "-p",
                "127.0.0.1:18000:8000",
                "-e",
                f"POSTGRES_DB={source}",
                "-e",
                "CELERY_BROKER_URL=redis://redis:6379/15",
                "web",
                "gunicorn",
                "config.wsgi:application",
                "--bind",
                "0.0.0.0:8000",
                "--workers",
                "2",
                "--threads",
                "25",
                "--timeout",
                "120",
            )
            run("docker", "update", "--cpus", "2", "--memory", "1g", "--memory-swap", "2g", name)
            from urllib.request import urlopen

            for _attempt in range(30):
                try:
                    with urlopen("http://127.0.0.1:18000/api/v1/health/ready/", timeout=3):
                        break
                except OSError:
                    time.sleep(1)
            print("Measuring 50 concurrent HTTP clients.", flush=True)
            result = subprocess.run(
                [sys.executable, "backend/benchmark_review.py", "--fixture", str(fixture_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            (output / "benchmark.json").write_text(result.stdout, encoding="utf-8")
            if result.returncode:
                raise RuntimeError("Benchmark failed; inspect the saved benchmark.json report.")
            print(result.stdout)
        finally:
            run("docker", "stop", name)
    print("Review databases and dump retained for inspection; no user databases changed.")


if __name__ == "__main__":
    main()
