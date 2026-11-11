# Implementation and verification status

## Delivered

- Four responsive public views: dashboard, searchable topic catalog, topic detail/evidence, and methodology.
- UTC 24-hour, 7-day, 30-day and custom date comparisons, absolute/percentage sorting, distinct counts, newly-active handling, chart gaps, and accessible chart tables.
- Same-origin API proxy; FastAPI endpoints; PostgreSQL container configuration; SQLite-supported local tests.
- Automatic real-source collection with paginated creation/activity intervals, persisted jobs, retries, quota reserve, backoff, compressed-response support, reconciliation, and sampled answers.
- Automatic content-aware topics, optional local semantic embeddings, multilabel membership, unclassified evidence, taxonomy provenance, and retirement/lineage metadata.
- Immutable atomic dataset publication, source-content suppression, bounded retention, source status, and non-disruptive update polling.
- Docker Compose services, environment-file credentials, mounted-secret override, optional model-runtime override, health checks, persistent data volume, and CI.

## Verification performed

- Backend: **27 tests pass**, covering growth, coverage gaps, half-open intervals, overlapping memberships, immutable releases, paging, input validation, source suppression, provider failures/backoff/gzip/quota reset, classification, retention, search, credentials and publication capacity.
- Frontend: production build and TypeScript check pass.
- Browser: rendered dashboard and topic catalog, topic detail, correct 40→60 / +20 / +50% example, 30-day period change, question preview, Escape dismissal with focus restoration, and responsive layouts inspected.
- Real API smoke: fetched a bounded public interval, then ran a separate one-day worker cycle. It collected **40 real questions**, published a non-demo release, and correctly reported partial comparison coverage. This was a separate local database, not seeded into production or the demo.
- Compose files: parsed successfully and service/port isolation checked. Container execution has **not** been verified locally because Docker is not installed. CI includes an actual Docker build/start and HTTP smoke test for a Docker-equipped runner.

## Explicit differences from the target architecture

This is a runnable initial implementation, not evidence that the production quality and scale targets in the technical plan have been met.

1. Releases currently store bounded immutable JSON snapshots in PostgreSQL rather than normalized release-fact tables and precomputed bucket tables. Requests calculate counts from the pinned snapshot. `MAX_SNAPSHOT_QUESTIONS` defaults to 100,000; exceeding it preserves the last valid release and reports the worker failure. This is a guard, not a proven capacity target. Migrate to indexed relational release facts before large-scale deployment; the one-million-question load target has not been tested.
2. The default classifier is provisional lexical grouping. Local Sentence Transformers embeddings can be enabled with the model override. The held-out classification/search quality gates require a reviewed corpus and have not been measured. Search currently uses normalized terms and technical aliases, not semantic reranking.
3. Classification is rebuilt for publication over retained data to keep both comparison periods consistent. It is not yet optimized into daily discovery plus incremental assignment. Keep one worker.
4. Initial database tables are created from the versioned source models. A schema SQL reference is included; future schema alterations require explicit migrations. There is no automated upgrade migration framework yet.
5. Full releases are retained for seven days. The planned 90-day daily checkpoint tier and separate seven-day raw-response archive are not implemented. Publication snapshots retain source evidence for reproducibility within their retention period.
6. Answer enrichment intentionally samples up to three answers per question. Missing source IDs are suppressed as unconfirmed unavailability; absence is not treated as proof of deletion.
7. Operational backup/restore, external alert delivery, a 48-hour source soak, a complete WCAG audit, and production concurrency/performance targets remain deployment validation work. Runbooks and CI are supplied; no hosted deployment or credentials were created.

The original specification and technical plan remain the source of truth for further production hardening. No fabricated trend data is used in live mode.
