# Isolated release checks

This gate produces evidence, **not permission to deploy**. It never invokes `deploy.sh`, the existing Compose stack, live Shopee, or an existing database. It does not commit or push anything.

## Run

Prerequisites: Python 3.10+, a local Docker Engine using the default Unix-socket context, disk/RAM for a Python test image and ephemeral PostgreSQL 16, and network access to pull images/install dependencies during the build. No Compose plugin, `.env`, production credentials, or production Docker access is needed. Run only on a trusted local/test daemon (Docker access itself is powerful).

```sh
cd /opt/shopee-sandbox
python3 scripts/release_check.py
```

Optional **read-only source** drift comparison; this does not access production Docker or a database:

```sh
python3 scripts/release_check.py --production-source /opt/shopee
# Inventory/drift only, explicitly NOT a passing test run:
python3 scripts/release_check.py --static-only --production-source /opt/shopee
```

CI uses the same first command on a GitHub-hosted runner for pull requests and manual workflow dispatch, with read-only repository permission and no deployment environment/secrets. Require its check in branch protection separately. CI without a production checkout records that drift comparison is unavailable; it does not invent a clean comparison.

## Isolation and evidence

- A UUID names every run, test image, container, and private internal network. There are no published ports, host networking, external networks, Docker socket mounts, source bind mounts, or host database mounts.
- The build receives only a temporary positive-allowlist snapshot (application/tests/migrations/release scripts/workflows and named config files). It never receives `.env`, `.git`, backups, SQLite databases, caches or arbitrary root files. Symlinks in included trees are rejected. New asset types outside the allowlist must be explicitly reviewed/added in `scripts/release_check.py`.
- Never run `docker build -f Dockerfile.test .`: the driver is what makes the context safe. The existing production Dockerfile/Compose files are not changed or executed.
- SQLite tests run with `--network none`. PostgreSQL checks run on a fresh internal network and use only test credentials. PostgreSQL data is tmpfs; no durable database volume is created. Any image-created anonymous volumes are removed with their own container.
- Build-time dependency/image downloads are permitted; test runtime cannot reach Shopee/the internet. HTTP mocks remain required. No application lifespan/worker daemon is intentionally started by the driver.
- Cleanup removes only explicitly recorded UUID-named resources, in reverse order. No prune commands, shared image removals, or existing-container operations. Base images/build cache may remain. Cleanup errors fail the gate. SIGKILL/daemon failure can leave UUID-labelled resources; inspect the manifest/run name and remove **only that run's** resources manually, never prune the host.
- Evidence is written under `.release-checks/<run>/`: `manifest.json`, `drift.json`, phase logs, and (on successful SQLite completion) JUnit XML. The manifest starts failed and becomes passing only after both phases and cleanup succeed. Skipped/xfail tests fail the release policy; failing migrations, tests, image builds and cleanup are not ignored.
- The source SHA-256 hashes the sorted path-to-SHA-256 inventory of the exact snapshot, including dirty/untracked allowlisted files. Git HEAD is informational, not the tested identity. Source changes during the run fail the gate. Test image ID and PostgreSQL image ID are recorded. The test image is removed after the run; this is not a production image attestation.

## Checks

1. Existing full SQLite pytest baseline, using its unchanged fixtures; the repository's `test.db` is never mounted or touched.
2. A separate Python process against an initially empty PostgreSQL 16 database: Alembic upgrade to the single head, repeat upgrade, downgrade one revision and re-upgrade; verify revision and model tables/columns.
3. Real worker claim functions on PostgreSQL with two separate connections: lock the first eligible order/catalog row, require the claim to select the unlocked row within five seconds, and verify live leases are not double-claimed. This exercises `FOR UPDATE SKIP LOCKED` rather than only SQL compilation or a SQLite substitute. Catalog's singleton-running-job behavior is also asserted.
4. Native PostgreSQL catalog queries (legacy whitespace/variant normalization, cross-sibling filters and partner counts), preflight aggregation, collection persistence and bulk compare-and-swap updates.
5. Static allowlisted file hashes versus an optional local production checkout. Report contents are hashes and paths, not source/secret contents. Differences are expected and require review; they do not automatically fail the test phase.

## Manual production approval remains required

Before promotion, review the drift report (or obtain it from an authorized source-only checkout), all test evidence, intended configuration differences, database backup/restore readiness, migration/data compatibility, worker rollout ordering and rollback strategy. Recompute the allowlisted digest on the exact proposed release and compare it with a **passing** manifest. A later production build must use a separately reviewed safe context and bind its image digest to that reviewed source; this gate does not deploy or build a production artifact.

Known blockers in the existing deployment path, intentionally not modified here:

- `deploy.sh` can continue after a backup failure and its `curl` health check does not reject HTTP error statuses. Do not use its success message as release evidence.
- The existing Dockerfile uses `COPY . .`; a repository-root build can include sensitive/local files without an independently safe context.
- Production runtime/environment/DB drift is not observable from static source hashes; no secrets or live containers are inspected.

Limitations: this is a fresh-schema smoke test, not a production-data rehearsal; the rollback checks only the latest migration. Model comparison checks table/column presence, not every index, type, constraint or Alembic autogeneration diff. Worker checks cover row locks/live leases, not process crashes, full lease expiry/retry/fencing scenarios or performance. Dependency transitive versions and base image tags are not fully pinned; recorded image IDs describe this run, not guaranteed future reproducibility. A passing gate still requires human approval. If the unchanged baseline fails under network isolation, fix/mock the responsible test through the test owner rather than weakening isolation or ignoring failures.
