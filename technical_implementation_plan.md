# Technical implementation plan

Based on `product_design_specification.txt`, version 1.0, November 8, 2026.
Status: proposed engineering baseline; implementation has not started.

## 1. Scope and starting point

The project currently contains the product specification, a short Hungarian assignment description, and two screenshots in `kepek/`. There is no application, dependency manifest, database, or existing architecture to preserve. This plan translates the written specification into an implementable MVP. The screenshots have not been treated as approved visual requirements; the specification establishes behavior and visual principles but does not define exact typography, spacing, or component designs.

Build a public, account-free analytics application covering Stack Overflow initially. Deliver all four public sections: Dashboard, Explore Topics, Topic Details, and About & Methodology. Questions are the counting unit; answers and tags enrich classification and exploration. Never label observed question activity as a count of unique defects.

Keep watchlists, notifications, exports, a public administration interface, additional communities, and advanced cross-topic reporting outside the MVP. Include a small dashboard comparison chart because section 9.2 already requires inspecting selected topics; defer the richer comparison workspace from Phase 2.

## 2. Engineering handoff coverage

| Section 19 responsibility | Planned implementation | Completion evidence |
|---|---|---|
| Application architecture and components | Modular backend, web frontend, isolated background worker; section 3 | Services run locally with documented boundaries |
| Source data acquisition and processing | Resumable creation/activity ingestion, normalization, reconciliation; section 4 | Replays and source failures do not corrupt counts |
| Data storage and historical retention | Relational schema, immutable analytics releases, explicit retention; section 5 | Migrations, restore drill, historical query checks |
| Topic discovery, organization, maintenance | Automatic semantic grouping, multilabel assignment, versioned taxonomy; section 6 | Evaluated topics and safe taxonomy migration |
| Trend measurement implementation | Exact UTC windows, distinct counts, growth eligibility rules; section 7 | Formula and boundary tests |
| Data update and synchronization | Durable jobs, atomic release publication, non-disruptive polling; section 8 | Crash/retry and client-refresh tests |
| Interfaces between components | Versioned JSON API, generated contracts, job schemas; section 9 | Contract and integration tests |
| Infrastructure, deployment, monitoring | Container deployment, managed database, backups, operational alerts; section 11 | Staging deployment and recovery exercises |
| Automated testing and system performance | Requirement traceability, correctness tests, load and accessibility gates; sections 12–13 | AC-01 through AC-17 verified |

## 3. Architecture and components

### Recommended stack

Use a TypeScript/React frontend with Next.js App Router, a Python/FastAPI backend, and PostgreSQL. Python serves both the API and worker domain modules so classification and analytics share one implementation. FastAPI supplies an OpenAPI contract; generate the TypeScript client from it. Next.js handles pages and interactive presentation, not a second implementation of analytics. These frameworks provide the relevant routing and contract capabilities in their [Next.js documentation](https://nextjs.org/docs/app) and [FastAPI documentation](https://fastapi.tiangolo.com/features/).

Use PostgreSQL full-text search for names, descriptions, tags, aliases, and question titles, plus semantic reranking of topic candidates. PostgreSQL documents indexing, ranking, and configurations in its [full-text search documentation](https://www.postgresql.org/docs/current/textsearch.html). Store embeddings in PostgreSQL initially; exact similarity over the small topic catalog is sufficient. Add a vector index only after measured scale warrants it.

Use a locally executed, pinned sentence embedding model for classification and natural-language topic search. Select its artifact and license during the initial feasibility milestone. Sentence Transformers provides embedding-based similarity primitives; this is a capability, not evidence that any particular model will classify programming problems accurately. See its [semantic similarity documentation](https://www.sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html).

Use a PostgreSQL-backed job table with leases, retry scheduling, deduplication keys, and dead-letter status. Start with one scheduler and bounded worker concurrency; avoid an additional queue service until necessary. Use SQL migrations and a Python database layer with explicit transactions. Pin supported runtime and dependency versions at scaffolding, commit lockfiles, and record architecture decisions.

```text
Browser ── HTTPS ── Web frontend
                    │ /api/v1 (same-origin proxy)
                    ▼
                  API ── PostgreSQL (published data)
                           ▲
Scheduler ── durable jobs ── Worker
                            ├── Stack Exchange adapter
                            ├── normalize / classify
                            └── aggregate / validate / publish
```

The API never fetches Stack Exchange on a public request. Source outages therefore do not take down the browsing experience. The worker shares database and domain modules with the API but has separate resource limits and deployment processes.

Proposed repository structure:

```text
apps/web/                  Pages, UI components, URL state, generated API client
services/backend/app/      API, ingestion, topics, analytics, persistence modules
services/backend/tests/    Unit, database, provider-contract and pipeline tests
contracts/                Exported OpenAPI and fixture contracts
infra/                    Containers, local compose configuration, deployment
docs/                     Decisions, methodology, runbooks, test reports
```

## 4. Source acquisition and processing

### Acquisition strategy

Use the public Stack Exchange API as the MVP source. Register an application key; keep it server-side. Do not require public users to sign in. Start with a bounded 90-day backfill, giving room for two 30-day comparison windows and useful history. The feasibility milestone must measure whether the available quota can complete this backfill. If not, retain honest partial-history states while it progresses; a dump importer is a later alternative requiring separate access and format verification.

Implement a source adapter interface: fetch creation interval, fetch activity interval, refresh question IDs, fetch answer context, and fetch tag metadata. Source identity belongs in every key so future communities do not collide.

1. Fetch new questions by creation time using fixed, bounded intervals and ascending creation order. Request required fields through a verified API filter. Persist pagination progress and the raw batch identifier before normalization.
2. Follow `has_more` until the interval is exhausted. The documented maximum page size is 100; pagination completion is necessary before marking an interval covered. See [API paging](https://api.stackexchange.com/docs/paging).
3. Split large intervals to keep page counts bounded. Overlap neighboring source requests at timestamp boundaries and deduplicate locally. The domain uses half-open intervals regardless of provider endpoint boundary behavior.
4. Run a separate activity sweep for edits and answer/status changes, using activity-sort bounds. Creation-date bounds cannot substitute for an activity cursor. The API defines creation and activity sorts separately in the [questions endpoint](https://api.stackexchange.com/docs/questions).
5. Normalize UTC timestamps, source identity, tags, links, owner attribution, source status, question text, and engagement fields. Preserve source `answer_count`, `accepted_answer_id`, and `is_answered` independently; they describe different conditions. The [question type documentation](https://api.stackexchange.com/docs/types/question) exposes these fields and content-license metadata.
6. Retrieve available answer context using bounded, paginated enrichment jobs. Initially prioritize the accepted and highest-scored available answers. Store which answers were retrieved and when; never present a sampled answer set as exhaustive. Classification can run with question and tags while enrichment is pending, then schedule controlled reclassification.
7. Upsert by `(source_id, source_question_id)` and enqueue changed content for classification. Content hashes avoid repeated embedding work. Repeated jobs must produce the same state.

### Coverage and reconciliation

Maintain separate creation-ingestion, activity-refresh, answer-enrichment, and classification watermarks. A successful HTTP call is not a successful dataset update. A publishable interval requires fully traversed creation pages, validated writes, and a classification outcome for each question, including explicit Unclassified.

Coverage means observed public API coverage for a declared source and interval, not proof that every question ever created was visible. Posts deleted before collection may never be observed. Track coverage gaps and exclusions explicitly; do not infer complete coverage from the earliest and latest question timestamps.

Use overlapping sweeps and periodic rechecks of recent creation intervals to catch late availability. Refresh known IDs on a rotating schedule, prioritizing recently active questions. The [question-by-ID endpoint](https://api.stackexchange.com/docs/questions-by-ids) supports batched refreshing of up to 100 IDs. A missing ID after a successful request is initially `unavailable_unconfirmed`, not proof of deletion. Retry separately and distinguish confirmed closure/migration fields from uncertain removal. Suppress unavailable excerpts and links presented as available; retain an explanatory record when appropriate.

No API polling strategy guarantees immediate detection of every removal or edit. Expose last verification times and document this limitation. For confirmed removal, purge stored display content and derived embeddings, invalidate previews, and rebuild affected statistics according to the deletion policy in section 14.

### Quota and failure handling

Implement a shared provider limiter, per-method backoff, retry with jitter, request timeouts, and a quota reserve for ongoing ingestion. Never let backfill consume the live-update reserve. Read remaining quota from responses instead of assuming a daily entitlement. Honor the provider's `backoff` field and avoid semantically identical requests within a minute, as required by the [throttling documentation](https://api.stackexchange.com/docs/throttle).

Budget daily requests as creation pages + activity pages + reconciliation batches + answer/tag enrichment + backfill. Measure each component during the spike and project completion time. Set conservative concurrency and stop optional enrichment first when quota is low. A quota pause or provider failure leaves the last published release available with an accurate status.

Attribution is a release requirement: show source identity, original post link, author name/profile where supplied, and the applicable license link for displayed source content. Mark excerpts as excerpts and modifications as such. Preserve license metadata rather than assuming one version for all content; licensing can vary by contribution revision. Check [content licensing](https://stackoverflow.com/help/licensing) and [API terms](https://stackoverflow.com/legal/api-terms-of-use) during implementation and before deployment. Missing attribution metadata blocks excerpt publication until resolved; generated topic descriptions must be visually distinct from source text.

## 5. Data model and retention

| Entity | Essential fields and constraints |
|---|---|
| `sources` | Stable ID, API site parameter, name, URL, enabled state |
| `questions` | Composite source/question identity, creation/activity/edit timestamps, title/body, source URL, owner attribution, license, answer count, accepted-answer ID, source answered flag, score, source status, last verified time, content hash |
| `answers` | Source/answer identity, parent question FK, text, owner/license, score, acceptance flag, timestamps, fetch status |
| `tags`, `question_tags` | Source tag identity; unique question/tag membership; original labels preserved |
| `topics`, `topic_versions` | Stable topic UUID, slug/aliases, name, description, category, lifecycle status, taxonomy version, centroid/model provenance |
| `question_topics` | Unique `(taxonomy_version, question_key, topic_id)`, confidence, explanation features, assignment/model version |
| `topic_lineage` | Rename, split, merge, retirement events; predecessor/successor IDs and effective release |
| `ingestion_runs`, `coverage_intervals` | Source, query mode, boundaries, page checkpoints, attempts, error state, completeness, watermark |
| `jobs` | Type, schema version, deduplication key, payload, lease, attempts, next run, terminal error |
| `analytics_releases` | Immutable release ID, publication time, analysis end, taxonomy version, coverage manifest, validation result |
| `topic_buckets`, `source_buckets` | Release/version, UTC bucket bounds, topic/source, distinct counts, coverage state |
| `release_question_facts` | Release-pinned question membership and required listing/statistic fields, or equivalent immutable revision references |

Use immutable revision references rather than copying full text into every release. A release's counts and question explorer must derive from the same membership set. Source-removal suppression overrides historical content display immediately even if a release remains retained. Clearly annotate any retained snapshot whose evidence was subsequently removed.

Indexes: source/creation time, source/activity time, topic membership, topic/question creation time, job readiness, unique source IDs, and full-text search indexes. Keep exact question identities available to reproduce aggregate counts. Partition large time-series/fact tables only after measured need.

Proposed MVP retention policy:

- Keep 13 months of normalized question facts, memberships, and buckets, subject to content-removal requirements. Initially only the backfilled 90 days exist; retention does not manufacture earlier history.
- Keep current source text and attribution within that retained period; retain prior text revisions only when needed for an active release/reclassification, with a 30-day cleanup target.
- Keep raw response batches for 7 days for recovery and debugging, with restricted access and removal propagation. Avoid unnecessary user-profile fields.
- Keep detailed analytics releases for 7 days, daily reproducibility checkpoints for 90 days, and taxonomy lineage for the life of retained analytics. Explain expired snapshot links and offer the current release.
- Back up daily with a proposed 30-day backup window; document delayed expiry of removed content in inaccessible backups and reapply tombstones after restore.

Label totals as “Total observed in retained coverage” with explicit bounds. All-time source totals are not available. Retention expiration and source removal must never appear as new question activity; new releases carry revision notes when historical counts change.

## 6. Automatic topic discovery and maintenance

### Pipeline

1. Sanitize HTML and extract plain text. Preserve meaningful technical tokens such as C++, C#, package names, and error codes. Weight title and question text above tags and bounded answer context.
2. Compute a versioned embedding plus lexical/tag features. Store model artifact hash, preprocessing version, and source content hash. External model calls are not required for the MVP.
3. Bootstrap candidate clusters from the backfill using density-based clustering over embeddings, with tag and phrase coherence checks. Avoid an all-pairs question similarity matrix; benchmark approximate-neighbor clustering or bounded batches for the observed corpus size.
4. Generate labels and descriptions automatically from distinguishing tags, representative phrases, and medoid questions. Prefer labels such as a technology plus a problem phrase. Reject labels that only repeat generic words or produce incoherent summaries. Rejected candidates remain Unclassified until an automatic improvement passes quality checks.
5. Assign incoming questions to multiple topic centroids using calibrated similarity thresholds and lexical compatibility. Store reasons such as matching phrases/tags and representative evidence. Enforce uniqueness per topic, not exclusivity across topics.
6. Group accumulated unmatched questions periodically to discover new topics. Small groups can be surfaced as provisional when coherent; do not hide low-volume topics solely because they are small. A single unclear question may remain Unclassified.
7. Compute related topics from shared question membership and semantic similarity, excluding self-links and requiring a meaningful relationship. Store explanation signals for inspection.

Start candidate clustering nightly, incremental assignment after each ingestion batch, and taxonomy publication no more than daily. These are proposed cadences to tune through workload measurement. Topic discovery and assignment are automatic; human evaluation is a release-quality check, not routine manual category creation.

### Quality gates

Build a held-out, manually reviewed evaluation sample of at least 200 diverse questions and 30 candidate topics. Proposed launch targets: at least 85% of sampled assignments judged relevant, at least 80% of names/descriptions judged understandable, and relevant topics in the top five for at least 80% of a 30-query search set. Track unclassified share, duplicate topics, coverage across technical categories, and assignment churn; report results rather than claiming universal accuracy. Tune thresholds on a separate calibration set.

Do not publish a failed taxonomy solely to reduce the Unclassified percentage. If semantic grouping cannot meet the gate, improve preprocessing, model choice, or labeling before launch. A fixed tag catalog is not an equivalent replacement for automatic content-aware organization.

### Historical consistency

Cosmetic renames preserve topic identity and aliases. Definition changes, splits, merges, assignment changes, and model changes create a new taxonomy version. Reclassify the full retained comparison/history range under that version, recompute aggregates in staging, and atomically publish only after validation. Both sides of every comparison must use the same definition.

Display “History recalculated under topic definition version …” when applicable. Preserve predecessor/successor links for retired topics. A newly discovered topic with historical matches is not newly active merely because its name is new. If reclassification of the required range is incomplete, continue serving the previous valid release; show the pending taxonomy only as non-comparable, if exposed at all.

## 7. Trend measurement and ranking

### Windows and counts

Use UTC for storage and analysis. Display explicit UTC bounds throughout; localized dates may be secondary labels. Set release boundary `T` to the latest completed hour for which the pipeline has a usable snapshot. The UI states the exact endpoint and lag, rather than implying the window ends at the user's current second.

For duration `D` of 24 hours, 7 days, or 30 days:

```text
current  = [T - D, T)
previous = [T - 2D, T - D)
C = count(distinct source/question identity in current and topic)
P = count(distinct source/question identity in previous and topic)
absolute_growth = C - P
relative_growth = 100 * (C - P) / P, only when P > 0 and coverage is valid
```

Creation time, not ingestion or answer time, determines membership in a period. Use exact timestamp predicates for arbitrary date ranges; hourly buckets accelerate aligned presets, while boundary fragments require raw fact queries. Custom range duration defines an equally long preceding comparison. Reject ranges outside retained bounds or show explicit partial coverage; cap public range length to the supported retention window.

| Condition | Display and ranking behavior |
|---|---|
| Complete comparable coverage, P > 0 | Signed absolute and relative changes; positive-growth topics eligible for growing rankings |
| Complete comparable coverage, P = 0, C > 0 | “Newly active”; percentage is null; separate emerging section |
| Complete coverage, P = 0, C = 0 | “No activity”; percentage is null |
| Complete coverage, P > 0, C = 0 | Negative absolute change and -100% |
| Missing/incomplete baseline or current coverage | Observed counts may appear with limitations; growth is null and excluded from authoritative rankings |
| Older complete release | Historical comparison remains valid at its displayed boundary, with an outdated badge |

Do not collapse numeric zero and missing data. A complete zero-question bucket has count 0; an unavailable bucket has null. Partial buckets may expose an observed count plus a distinct incomplete flag, never a normal zero-filled chart point.

### Ranking and summary semantics

Default: positive absolute growth descending, then current volume descending, then stable topic ID. Relative ranking includes only positive finite percentage changes, with the same tie breakers. Newly active topics can appear in absolute rankings when both windows are covered, but never receive an invented percentage. Show baseline volume alongside percentages; use a configurable “Small baseline” notice, initially P < 10, without suppressing discovery.

“Growing topics” counts topics with valid positive absolute change. “Fastest-growing topic” follows the selected ranking and can be empty. “Newly active topics” requires genuine zero baseline under complete coverage. “New Questions” is a distinct question count across the active source/category filters, not a sum of topic counts. A question matching several selected categories/topics counts once in a combined total. Unclassified questions count in overall source activity but not named-topic rankings.

Question-status statistics refer to questions created in the selected period and their latest status within the release; label that scope. Show zero answers, one or more answers, accepted answer, and source answered status separately. These indicators may overlap and never change growth counts.

Historical charts use hourly buckets for 24-hour views and daily buckets for longer views; label partial endpoint days. Default topic history spans both comparison windows. Offer bounded historical date controls. All chart series use the same release and taxonomy. Display gaps, period boundaries, units, baseline context, and accessible data tables; never connect across missing data as though observed.

## 8. Updates and synchronization

Proposed schedules: creation/activity polling every 10 minutes, incremental processing continuously, hourly analytics releases, daily discovery, and quota-budgeted rotating reconciliation. Target publication of newly observed questions within 90 minutes under normal source and workload conditions. Show source last success, data-covered-through, and analytics publication time separately. This target is a proposed product decision, not a guarantee established by the specification.

Job lifecycle: ready → leased → completed, or retry → dead-letter. Claim jobs transactionally, renew leases for long work, recover expired leases, and use idempotency keys. Never advance an interval watermark until all pages and writes commit. Prevent a late retry from overwriting a newer source revision.

Publisher lifecycle: capture inputs → build immutable candidate release → validate coverage, counts, taxonomy and evidence → transactionally change the active-release pointer. If any step fails, retain the previous release. Publish changes to question status at the next coherent release; source-removal display suppression is immediate.

The frontend polls a lightweight release/status endpoint every 60 seconds while visible and pauses in background tabs. Fetch all replacement widgets for one release before committing the UI update. Preserve URL filters, keyboard focus, scroll position, expanded question, and selected chart series. During an active preview or interaction, show “Updated data available” and defer disruptive reordering until the interaction ends or the user applies the update. Explicit refresh respects the same state preservation.

Use ETags and cache keys containing release ID, taxonomy, and normalized filters. Never combine cached current counts with fresh previous counts. Suggested freshness labels: current up to 90 minutes of pipeline lag; outdated above that; unavailable when no usable release exists. Partial coverage is an independent flag that can coexist with outdated data. Provider outage status can coexist with a valid cached release.

## 9. Component interfaces

Public API prefix: `/api/v1`. Read-only public access; no user accounts in Phase 1.

| Endpoint | Responsibility |
|---|---|
| `GET /status` | Active release, source health, coverage bounds, freshness, update timestamps |
| `GET /dashboard` | Coherent summary cards, rankings, emerging topics, overall history |
| `GET /topics` | Search/filter/sort catalog with cursor pagination |
| `GET /topics/{id}` | Topic definition, tags, scoped statistics, lineage and related topics |
| `GET /topics/{id}/history` | Bucketed history with coverage flags and period boundaries |
| `GET /topics/{id}/questions` | Evidence list; newest/oldest/activity/score sorting and text search |
| `GET /questions/{source}/{id}` | Preview, available answer excerpts, attribution and verification state |
| `GET /methodology` | Metric definitions, source coverage, taxonomy/model versions and limitations |

Common query parameters: `release_id`, `source`, `period`, optional `from`/`to`, `category`, `sort`, `cursor`, and `limit`. Accept only documented sort/filter values. `period` and custom dates are mutually exclusive. Default period is 7 days and default ranking is absolute growth. Limit pages to 100 records. Bind cursors to release and normalized query; reject mismatched or expired cursors with a recoverable message.

Every analytical response includes:

```text
meta: { release_id, taxonomy_version, as_of, published_at,
        current_window, previous_window, coverage_state,
        freshness_state, limitations[] }
metrics: { current_count, previous_count, absolute_growth,
           relative_growth_pct, growth_state }
```

Use null for unavailable metrics with a machine-readable reason. A valid response with incomplete data is a 200 response with metadata, not an invented error or zero. Use 422 for invalid query input, 404 for unknown entities, 410 for expired immutable releases with a current-release reference, and 503 when no usable dataset exists. Include request IDs and safe error messages.

Jobs use typed payloads with `schema_version`, source ID, interval or entity IDs, input revision, and idempotency key. Contract changes require regenerated client types and fixture validation. Keep source parsing inside the adapter so provider changes do not leak into UI models.

## 10. Frontend implementation and visual behavior

Create shared design tokens for spacing, type scale, neutral surfaces, borders, focus rings, and semantic trend states. Produce responsive wireframes before detailed styling; the written specification does not prescribe exact colors or fonts. Use signs, labels, and icons alongside color. Target WCAG 2.2 AA as specified, verified through automated and manual accessibility checks.

| Route | Components and behavior |
|---|---|
| `/` | Global header/search/source status; introduction; analysis controls; four summary cards; top-ten ranked list; activity chart with up to three selected topics; emerging topics; coverage panel |
| `/topics` | Search, category/source/period/activity/growth filters, all specified sorts, paginated topic cards, useful empty state and clear-filters action |
| `/topics/{id}/{slug}` | Name/description/category/tags; metrics; historical chart; searchable/sortable question list; optional preview with bounded answer excerpts; related topics; taxonomy history notice |
| `/about` | Source and license information; formulas and worked example; overlapping-topic explanation; coverage and update policy; topic-generation method; interpretation limitations |

Encode applicable filters in URL query parameters. Preserve source, period/custom dates, and category across navigation; list-only search/sort remain page-specific and restore through browser back. Global search navigates to Explore. Canonical topic identity is its ID, so renamed slugs redirect safely.

Build shared `AnalysisControls`, `DataStatus`, `MetricCard`, `GrowthValue`, `TopicList`, `HistoryChart`, `QuestionList`, `QuestionPreview`, `Attribution`, and `EmptyState` components. Fetch keys include all applicable filters. Cancel obsolete requests so a slow earlier search cannot overwrite later results. Debounce search briefly without delaying explicit submission.

States to design and test: initial skeleton; empty dataset; no search results; newly active; low baseline; insufficient history; partial coverage; outdated release; provider unavailable; retired topic; unavailable source question; API error with retry. Clearly distinguish no matching questions from no reliable data.

On mobile, stack summary cards, keep filter access compact, use readable ranked cards or an explicitly scrollable table, and preserve chart units. Use semantic tables/headings, labeled controls, visible focus, skip navigation, keyboard-accessible charts or equivalent tables, polite update announcements, and reduced-motion preferences. Preview dialogs must trap focus, close with Escape, and restore focus to their trigger. Do not make chart hover the only way to inspect values.

## 11. Infrastructure, security, and operations

Local development: containerized PostgreSQL plus web/API/worker services, migrations, deterministic fixture data, and a clearly marked demo mode. Synthetic data never enters production rankings or masquerades as source data.

Production baseline: container-capable hosting for web, API and worker, managed PostgreSQL with backups, TLS termination, and a scheduler process. Keep the worker alive independently of HTTP requests. Select the hosting vendor after workload and budget measurements; no vendor account or deployment is assumed by this plan. Use distinct development/staging/production databases and secrets.

Deployment pipeline: dependency/type checks → tests → builds → migration compatibility check → staging smoke tests → production rollout. Use expand/contract migrations, backward-compatible schema changes, and separate taxonomy activation. Roll back application images and the active analytics pointer independently; do not blindly reverse destructive database migrations.

Security: sanitize all source HTML with an allowlist, block script/event-handler content, validate outgoing source/profile links, escape titles, set a restrictive content security policy, parameterize SQL, and bound query cost. Treat source text as untrusted data, including in classification. Protect worker/admin commands; expose no unauthenticated write endpoints. Keep database and provider credentials out of the client, logs, fixtures, and repository. Rate-limit public search and preview endpoints. Store minimal author metadata needed for attribution.

Monitor API latency/errors, ingestion lag by stage, coverage gaps, quota remaining, backoff durations, queue age, dead-letter jobs, classification throughput/unclassified share, taxonomy churn, release age, and database growth. Use structured logs with request/run/release IDs and no full source bodies by default.

Alert when no successful publication occurs for 90 minutes, queue lag threatens the freshness target, usable coverage falls unexpectedly, quota reserve is threatened, or repeated publication validation fails. Thresholds must be tuned after the staging soak. Provide runbooks for source outage, exhausted quota, bad taxonomy, stalled worker, failed migration, removal request, and restore.

Proposed recovery targets: database RPO of 24 hours with daily backups and RTO of 4 hours; improve RPO with point-in-time recovery when hosting permits. Validate these through a staging restore and idempotent source replay. These are operational targets, not yet measured capabilities.

## 12. Testing, acceptance, and performance

### Test layers

- Unit/property tests: equivalent windows, boundaries, UTC/DST independence, zero/negative baselines, null coverage, distinct counting, ranking tie breaks, retention boundaries, and stable identifiers.
- Database tests: uniqueness/FKs, idempotent upserts, transactional cursors, job leases, concurrent retries, atomic publication, immutable membership, and stale revision protection.
- Adapter tests: recorded sanitized fixtures for paging, backoff, quota exhaustion, malformed responses, missing owners/licenses, unavailable IDs, and partial answer enrichment. A separately enabled live smoke test verifies the provider contract without making CI depend on it.
- Pipeline tests: ingest → normalize → classify → aggregate → publish → API, including late arrivals, edits, removals, restarts mid-page, taxonomy changes, and recovery from failed releases.
- Frontend end-to-end tests: filters, navigation, search, all data states, source links, URL restoration, request races, and updates during previews.
- Accessibility: automated checks plus manual keyboard and screen-reader checks of controls, chart alternatives, tables, and dialogs at mobile/tablet/desktop widths.
- Topic/search evaluation: held-out quality gates from section 6, reproducibility under pinned model versions, and comparison of changes before taxonomy activation.

### Acceptance traceability

| Acceptance ID | Required verification |
|---|---|
| AC-01 | New source fixture appears in evidence and increments its creation-time bucket after publication |
| AC-02 | All original tags survive ingestion and display |
| AC-03 | Multi-topic membership increments each topic once and overall unique total once |
| AC-04 | P=40, C=60 produces +20 and +50% in API and UI |
| AC-05 | P=0, C=12 with complete coverage produces Newly active and null percentage |
| AC-06 | Switching 7 to 30 days updates all widgets and comparison boundaries together |
| AC-07 | Absolute/relative rankings differ on an intentional fixture and show active sort |
| AC-08 | Topic route renders definition, metrics, chart and matching evidence |
| AC-09 | Question links use the correct preserved source URL |
| AC-10 | Unknown search shows explanation and filter reset |
| AC-11 | Provider outage retains marked cached data and never fills gaps with zero |
| AC-12 | Incomplete interval qualifies counts and excludes unreliable growth |
| AC-13 | Release refresh changes data without a full reload or loss of focus/filter/preview state |
| AC-14 | Split/merge/reassignment recalculates both windows under one version and shows revision context |
| AC-15 | Dashboard usable at 360px, 768px and desktop widths |
| AC-16 | Complete dashboard-to-source journey using keyboard only |
| AC-17 | Question and answer excerpts each retain source, author and applicable license information |

Functional coverage: FR-01–07 map to ingestion/status/attribution; FR-08–15 to taxonomy and evidence; FR-16–24 to analytics/coverage; FR-25–32 to routes, search, sorting, filters and URL state. Release review must verify the page-specific and nonfunctional requirements in sections 6–15 as well as the numbered criteria.

### Proposed performance budgets

Test initially with 1 million retained questions, 3 million topic memberships, and 1,000 topics; revise the fixture after observing actual volume. This is a capacity test scenario, not a source-volume estimate.

- Cached dashboard and topic summaries: p95 server response under 500ms at 20 concurrent users.
- Search and paginated evidence: p95 under 1 second for bounded queries at the same concurrency.
- Primary dashboard content visible within 2.5 seconds on a defined mobile test profile; document device/network conditions.
- Sustained processing capacity at least twice the measured incoming workload; complete the hourly aggregation before the next publication slot.
- No unbounded response payloads, offset scans over deep question lists, or all-corpus embedding work in the request path.

Use query plans, indexed distinct counts, immutable cached aggregates, cursor pagination, lazy-loaded chart code, and bounded embedding batches. Measure before adding Redis, distributed workers, a vector database, or a separate search engine.

## 13. Delivery sequence and exit gates

Estimates are planning ranges for one experienced full-time engineer, excluding waiting for provider access or stakeholder review. Allow approximately 9–13 engineer-weeks in total and revise after the first milestone; classification quality and backfill access are the largest uncertainties.

| Milestone | Work and deliverables | Dependencies | Estimate | Exit gate |
|---|---|---|---|---|
| M0: Feasibility and decisions | API/key/filter spike, volume/quota/backfill estimate, sample topic model, retention/freshness decisions, page wireframes | None | 1 week | Source pipeline feasible; explicit decision log and model baseline |
| M1: Foundation | Repository, containers, schema/migrations, job runner, API contract, CI and fixture dataset | M0 | 1 week | Local web/API/worker boot; typed fixture route works |
| M2: Acquisition | Creation/activity streams, tags/answers, checkpoints, reconciliation, coverage and attribution | M1 | 1–2 weeks | Replay/failure tests pass; bounded backfill progressing |
| M3: Topic system | Features, discovery, names/descriptions, multilabel assignment, search, taxonomy lineage and reprocessing | M2 sample data | 2–3 weeks | Held-out quality gates and versioned rebuild pass |
| M4: Analytics | Exact windows, buckets, rankings, coverage eligibility, immutable releases, evidence consistency | M2–M3 | 1–2 weeks | Metric/coverage/atomic-publication tests pass |
| M5: Public experience | Four pages, controls, charts, evidence previews, update behavior, responsive/accessibility work | M1 contract; final integration M4 | 2 weeks | AC-04–10 and AC-13–17 pass against real pipeline |
| M6: Release readiness | Staging soak, remaining ACs, load/security/attribution checks, restore drill, methodology/runbooks | M2–M5 | 1–2 weeks | All ACs pass; 48-hour healthy soak; measured targets or documented release decisions |

UI implementation can start against contract fixtures after M1, but real metric correctness remains a release gate. Do not substitute simulated data for completion of M2–M4. Backfill at least 60 contiguous days before claiming complete 30-day comparisons; if this is not possible, release only with explicit insufficient-history states and a documented scope decision.

First implementation tasks: scaffold the services and database; build the source-feasibility harness; establish coverage and question schemas; implement one bounded creation interval with replay tests; classify a sample; publish one traceable topic metric through a minimal API. This vertical slice validates the central value before polishing the full dashboard.

## 14. Product decisions, risks, and unresolved inputs

These are proposed defaults for review, not requirements silently added to the specification. Record final decisions in architecture/product decision records before the affected release.

| Decision or uncertainty | Proposed treatment and consequence |
|---|---|
| Freshness and period endpoint | Hourly completed boundaries, 10-minute source polling, 90-minute healthy freshness target; clearly show exact UTC bounds |
| Initial historical coverage | Attempt 90-day backfill; require 60 covered days for full 30-day comparisons; incomplete history never qualifies as zero baseline |
| Incomplete coverage | Show qualified observed counts; withhold growth/ranking eligibility rather than extrapolating missing activity |
| Topic changes | Recompute full retained history under one taxonomy; preserve lineage and disclose recalculation |
| Source removals | Remove display content immediately when confirmed, then recompute affected historical counts with revision disclosure; current observed totals can change retrospectively |
| Low-volume topics | Retain discovery; show baseline counts and initial small-baseline notice below 10 questions |
| Retention | 13-month fact/history window; label total observed scope and show retention boundaries |
| Answer coverage | Bounded available answer context and excerpts; explicitly state enrichment completeness; source answer count remains authoritative metadata |
| Category model | Small stable technical category vocabulary for filtering, automatically assigned to discovered topics; it does not replace automatic topic discovery |
| Dashboard comparison scope | Up to three selected topics for the section 9 chart; advanced comparison workspace deferred |
| Classification quality | Local embedding/clustering with automatic extractive labels; failure to meet quality gates delays taxonomy launch |
| Operating budget and hosting | Vendor and resources selected after measured volume; no assumed paid model provider or GPU |

Outstanding project inputs are deployment budget/host, delivery deadline, ownership of source credentials, and final visual branding. They do not block writing or beginning the technical implementation. Validate legal/source usage requirements against current official terms before production. No application code or deployment has been performed as part of this planning task.
