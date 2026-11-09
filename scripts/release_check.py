#!/usr/bin/env python3
"""Run isolated release checks; never invoke the deployment stack."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"requirements.txt", "alembic.ini", "pytest.ini", "Dockerfile", "Dockerfile.test",
              "docker-compose.yml", "deploy.sh", "entrypoint.sh", "RELEASE_CHECKS.md"}
TREES = {"app", "alembic", "tests", "scripts", ".github"}
SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".mako", ".sh", ".yml", ".yaml",
            ".png", ".jpg", ".jpeg", ".svg", ".ttf", ".otf", ".woff", ".woff2"}


def source_files(root):
    """Positive allowlist: never traverse backups, .git, .env, or local databases."""
    result = {}
    for name in sorted(ROOT_FILES | TREES):
        base = root / name
        if base.is_symlink():
            raise RuntimeError(f"Symlink not allowed in release source: {name}")
        if not base.exists():
            continue
        paths = base.rglob("*") if base.is_dir() else [base]
        for path in sorted(paths):
            rel = path.relative_to(root)
            if any(p.startswith(".") and p != ".github" for p in rel.parts):
                continue
            if "__pycache__" in rel.parts:
                continue
            if path.is_symlink():
                raise RuntimeError(f"Symlink not allowed in release source: {rel}")
            if path.is_file() and (name in ROOT_FILES or path.suffix in SUFFIXES):
                result[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def drift_report(files, production):
    report = {"production_comparison": "not supplied; must be reviewed before release", "differences": []}
    if production:
        if not production.is_dir() or not (production / "app").is_dir():
            raise RuntimeError("Production source must be an existing application checkout")
        other = source_files(production)
        report["production_comparison"] = "static allowlisted files only; not runtime state"
        report["production_source_digest"] = digest(other)
        report["differences"] = [
            {"path": p, "sandbox_sha256": files.get(p), "production_sha256": other.get(p)}
            for p in sorted(files.keys() | other.keys()) if files.get(p) != other.get(p)
        ]
    report["manual_release_blockers"] = [
        "Review static drift, intended environment differences, migration compatibility and rollback backup.",
        "Existing deploy.sh tolerates backup failure and uses curl without HTTP status failure checks; do not treat it as a release gate.",
        "Existing Dockerfile COPY . requires a separate safe production context; these checks do not authorize its repository-root build.",
        "A human must approve deployment of exactly the tested source digest; no deployment is performed.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-source", type=Path, help="optional read-only local source comparison (never reads .env)")
    parser.add_argument("--static-only", action="store_true", help="write drift/source inventory, without claiming tests passed")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    name = "shopee-release-" + uuid.uuid4().hex
    output = ROOT / ".release-checks" / name
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "run": name, "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "failed", "deployment_authorized": False}
    resources = []
    cleanup_errors = []
    exit_code = 1
    try:
        files = source_files(ROOT)
        manifest.update(source_sha256=digest(files), files=files)
        (output / "drift.json").write_text(json.dumps(drift_report(files, args.production_source), indent=2) + "\n")
        git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
        manifest["git_head_informational"] = git.stdout.strip() if git.returncode == 0 else None
        if args.static_only:
            manifest["status"] = "static-only-not-tested"
            exit_code = 0
        else:
            if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
                raise RuntimeError("Use the local default Docker context, without DOCKER_HOST/DOCKER_CONTEXT")
            context_name = run(["docker", "context", "show"], capture_output=True, text=True).stdout.strip()
            endpoint = run(["docker", "context", "inspect", context_name, "--format", "{{.Endpoints.docker.Host}}"], capture_output=True, text=True).stdout.strip()
            if context_name != "default" or not endpoint.startswith("unix://"):
                raise RuntimeError("Release checks require a local default Unix-socket Docker daemon")
            with tempfile.TemporaryDirectory(prefix="context-", dir=output) as temp:
                context = Path(temp)
                for rel, expected in files.items():
                    target = context / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = (ROOT / rel).read_bytes()
                    if hashlib.sha256(data).hexdigest() != expected:
                        raise RuntimeError(f"Source changed during snapshot: {rel}; rerun")
                    target.write_bytes(data)
                image = name + ":tested"
                resources.append(["docker", "image", "rm", image])
                run(["docker", "build", "--label", f"release-check.run={name}", "--file", context / "Dockerfile.test", "--tag", image, context])
                manifest["test_image_id"] = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture_output=True, text=True).stdout.strip()
                run(["docker", "pull", "postgres:16"])
                manifest["postgres_image_id"] = run(["docker", "image", "inspect", "postgres:16", "--format", "{{.Id}}"], capture_output=True, text=True).stdout.strip()
                network = name + "-net"
                run(["docker", "network", "create", "--internal", "--label", f"release-check.run={name}", network])
                resources.append(["docker", "network", "rm", network])
                pg = name + "-pg"
                run(["docker", "create", "--name", pg, "--label", f"release-check.run={name}", "--network", network,
                     "--network-alias", "release-postgres", "--tmpfs", "/var/lib/postgresql/data:rw", "--env", "POSTGRES_USER=release",
                     "--env", "POSTGRES_PASSWORD=release-test-only", "--env", "POSTGRES_DB=release_checks", manifest["postgres_image_id"]])
                resources.append(["docker", "rm", "--force", "--volumes", pg])
                run(["docker", "start", pg])
                env = ["--env", "SHOPEE_BASE_URL=http://127.0.0.1:9", "--env", "SHOPEE_PARTNER_ID=0",
                       "--env", "SHOPEE_SHOP_ID=0", "--env", "SHOPEE_PARTNER_KEY=release-test-only",
                       "--env", "SESSION_SECRET_KEY=release-test-only", "--env", "API_KEY=release-test-only",
                       "--env", "ORDER_SYNC_ENABLED=false", "--env", "ORDER_RECONCILIATION_ENABLED=false",
                       "--env", "CATALOG_EXPORT_DIR=/tmp/release-exports"]
                for phase, net, db, command in [
                    ("sqlite", "none", "sqlite+aiosqlite:////tmp/release-app.db", ["python", "-m", "pytest", "tests", "-v", "-p", "scripts.release_check_pytest", "--junitxml=/results/sqlite.xml"]),
                    ("postgres", network, "postgresql+asyncpg://release:release-test-only@release-postgres:5432/release_checks", ["python", "scripts/release_check_postgres.py"]),
                ]:
                    container = name + "-" + phase
                    run(["docker", "create", "--name", container, "--label", f"release-check.run={name}", "--network", net,
                         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", *env, "--env", "DATABASE_URL=" + db,
                         manifest["test_image_id"], *command])
                    resources.append(["docker", "rm", "--force", "--volumes", container])
                    with (output / (phase + ".log")).open("w") as log:
                        run(["docker", "start", "--attach", container], stdout=log, stderr=subprocess.STDOUT)
                    # Docker start --attach status is not trusted as the sole test exit status.
                    status = run(["docker", "inspect", container, "--format", "{{.State.ExitCode}}"], capture_output=True, text=True).stdout.strip()
                    if status != "0":
                        raise RuntimeError(f"{phase} exited {status}; see {phase}.log")
                    if phase == "sqlite":
                        run(["docker", "cp", f"{container}:/results/sqlite.xml", output / "sqlite.xml"])
                    manifest[phase] = "passed"
                if source_files(ROOT) != files:
                    raise RuntimeError("Source changed while tests ran; rerun before approval")
                manifest["status"] = "passed-awaiting-manual-approval"
                exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        manifest["error"] = str(exc)
        print(f"Release checks failed: {exc}", file=sys.stderr)
    finally:
        for command in reversed(resources):
            try:
                run(command)
            except (Exception, KeyboardInterrupt) as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            manifest["cleanup_errors"] = cleanup_errors
            manifest["status"] = "failed"
            exit_code = 1
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Release evidence: {output}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
