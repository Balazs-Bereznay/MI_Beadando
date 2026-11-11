# Operations runbook

## Startup and diagnosis

Run `docker compose ps` and `docker compose logs --tail=100 api worker web` from the project root. Check the public `/api/v1/status` endpoint for coverage, active release, source health, and freshness. Container health means the service responds; it does not establish source coverage. A new installation can be healthy while its initial backfill is incomplete.

The database is internal to Compose. Do not expose port 5432 or the backend publicly just to troubleshoot. Use `docker compose exec` for internal commands. Keep one scheduled worker until concurrent scheduling is explicitly supported and tested.

## Source outage or exhausted quota

Inspect the worker logs and source-health status without printing credentials. Backoff and quota state persist in the database. The worker retries automatically while the public app continues serving its last published release. Do not delete coverage records or force complete flags to clear an alert. Allow a normal collection/reconciliation cycle to restore freshness. A long provider outage is not zero question activity.

## Failed worker

Restart only the worker: `docker compose restart worker`. Leased jobs can be reclaimed after expiry; completed pages are idempotent. Inspect dead-letter work before replaying it. A persistent provider-format or classification exception requires a code fix; repeated restarts alone will not fix the underlying issue.

## Database backup

Back up from inside the database container to avoid host shell binary redirection differences:

```text
docker compose exec db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/trends.dump'
docker compose cp db:/tmp/trends.dump ./trends.dump
```

Move the dump to restricted backup storage and remove the temporary copy after verifying it. Backups contain source data and attribution metadata. Do not add them to Git. Schedule backups through the deployment host's backup system, retain them according to policy, and test restoring to an isolated instance. Managed database point-in-time recovery is preferable where available.

## Restore drill

Use a separate Compose project with a separate port and volume. Restore a backup into its empty database with `pg_restore` before starting its API/worker. Verify status, representative topic counts, evidence links, and data mode. Reapply any content-removal records made after the backup. Only then consider a production restore. Never experiment with `pg_restore --clean` against the current production database.

## Deployment and rollback

Build immutable image versions, run tests, deploy to staging, and verify source health plus a complete publication. Keep a known-good image version available. Application rollback does not roll back database data. The initial schema is created on startup; subsequent schema changes need explicit, reviewed migrations before deployment. Never assume `create_all` alters existing tables.

## Content and taxonomy corrections

Source text is untrusted. Confirmed removals must suppress old previews as well as current data. Reclassification must rebuild both comparison windows together; do not edit individual published counts. Keep taxonomy provenance and explain retrospective revisions. A public administrative interface is outside this initial release; maintenance is performed by authorized operators.

## Production readiness

Use HTTPS, a non-default database password, restricted host secret files, monitoring, backups, and a resource budget. Establish external alerts for an unavailable status endpoint and data age beyond the intended freshness target. Run a 48-hour staging ingestion soak and the quality/performance evaluations in the technical plan before claiming those targets. The repository CI exercises application tests/builds and a Docker demo smoke test; it does not establish source availability or model quality.
