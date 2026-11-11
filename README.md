# Developer Observatory

A public dashboard for exploring growing developer question topics from Stack Overflow. Built with Next.js, FastAPI, a background ingestion worker, and PostgreSQL. The product and architecture are described in `product_design_specification.txt` and `technical_implementation_plan.md`.

## Start with Docker

Install Docker Engine/Desktop with Linux containers and Docker Compose **2.24 or newer**. From this directory:

```powershell
Copy-Item .env.example .env
# Edit .env to supply your Stack Exchange application key and settings.
docker compose up --build -d
```

On macOS/Linux, use `cp .env.example .env` instead. Open [localhost:3000](http://localhost:3000). Only the web service is published to the host; API and database stay on the internal Compose network. The default host binding is loopback. Use a TLS reverse proxy and an appropriate `BIND_ADDRESS` for a public deployment.

The stack has four containers: `web`, `api`, `worker`, and `db`. The API and worker use the same backend image. Database state persists in a named volume. Runtime secrets are **not** built into either image.

Default mode is **live**: the initial dashboard reports that data is not available while the worker collects and publishes it. A Stack Exchange application key is recommended for a useful request quota. No OpenAI or other paid model API key is required. Missing historical coverage is displayed honestly; the app never fills missing data with invented activity.

```powershell
docker compose logs -f worker
docker compose ps
docker compose down
```

`down` preserves the database. Avoid `down -v` unless you deliberately intend to delete collected data. Changing `POSTGRES_PASSWORD` after a volume has been initialized does not rotate the existing database password: change it in PostgreSQL and update the connection string together.

## Credentials and runtime configuration

The simplest setup uses the ignored `.env` file. Set `STACKEXCHANGE_API_KEY`, optionally `STACKEXCHANGE_ACCESS_TOKEN`, and database credentials there. Change `POSTGRES_PASSWORD` and the password in `DATABASE_URL` together; percent-encode special characters in the database URL. Use a non-default password for deployment. Keep `.env` private and do not paste rendered Compose configuration into public logs.

For file-mounted secrets, use the supplied override:

1. Create a private `secrets/` directory in the project root.
2. Write the database password to `secrets/database_password.txt`.
3. Write the full connection URL to `secrets/database_url.txt`, for example `postgresql+psycopg://trends:YOUR_ENCODED_PASSWORD@db:5432/trends`.
4. Write the Stack Exchange application key to `secrets/stackexchange_api_key.txt`.
5. Start using both files:

```powershell
docker compose -f compose.yaml -f compose.secrets.yaml up --build -d
```

These files are ignored by Git and mounted only into services that need them. The backend supports `DATABASE_URL_FILE`, `STACKEXCHANGE_API_KEY_FILE`, and `STACKEXCHANGE_ACCESS_TOKEN_FILE`. To use an access-token file, add another Compose secret and mount it into the worker with the corresponding `_FILE` variable. Restrict host file permissions. Docker Compose file secrets are mounted files, not an encrypted secrets vault. See the official [Compose secrets documentation](https://docs.docker.com/compose/how-tos/use-secrets/) and [environment-file documentation](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/).

The browser receives only public API responses. `API_INTERNAL_URL` is a server-side web setting and source credentials never use the `NEXT_PUBLIC_` prefix.

## Demo mode

Set `APP_MODE=demo` explicitly to explore deterministic sample data without source access. The interface labels it as synthetic demo data, and sample questions do not claim to link to real posts. Keep demo and live databases separate. For example, start the demo with a separate Compose project name so its volume is isolated:

```powershell
# In .env, set APP_MODE=demo, then:
docker compose -p developer-trends-demo up --build -d
```

Stop the other project first or choose a different `PORT`. Before returning to live mode, set `APP_MODE=live` and use the original project name. Never interpret demo metrics as real trends.

## Development and verification

Backend code is in `services/backend`, the frontend in `apps/web`, and the API shape in `contracts/api-shape.md`. Python 3.12 and Node 22 are the container runtime targets. The backend also supports SQLite for local tests; production Compose uses PostgreSQL.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r services/backend/requirements.txt
.venv/Scripts/python -m pytest services/backend/tests
cd apps/web
npm ci
npm run typecheck
npm run build
```

The backend's OpenAPI schema is available at `/openapi.json` inside the Docker network. Public application endpoints are under `/api/v1/` through the frontend's server-side proxy.

## Worker settings and topic models

| Setting | Default | Purpose |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | 600 | Schedule source creation/activity sweeps |
| `BACKFILL_DAYS` | 90 | Initial historical collection target |
| `QUOTA_RESERVE` | 200 | Pause optional work before exhausting acquisition quota |
| `RECONCILE_INTERVAL_SECONDS` | 3600 | Rotate rechecks of previously observed IDs |
| `ANSWER_ENRICHMENT_BATCH` | 3 | Questions enriched per answer-context cycle |
| `RETENTION_DAYS` | 395 | Retain source facts for approximately 13 months |
| `LOG_LEVEL` | INFO | Worker log level |

The lightweight default automatically groups questions using content, tags, and available answer context. It is explicitly identified as a provisional lexical classifier. It requires no model download or additional credentials. It is not a claim that semantic model quality gates from the plan have passed.

For local semantic embeddings, place a compatible SentenceTransformer model directory at `models/topic-encoder/`, then run:

```text
docker compose -f compose.yaml -f compose.model.yaml up --build -d
```

The override installs the optional model runtime and mounts the model read-only. Model loading uses local files only; no question text is sent to a model service. Select and license the model for your deployment, and evaluate the resulting assignments before relying on them. The optional dependency substantially increases image size and CPU/memory needs. You can combine this override with `compose.secrets.yaml`.

See [operations](docs/operations.md) for backup, source outage, recovery, and deployment guidance.
See [implementation status](docs/implementation-status.md) for tested behavior and explicit differences from the production-scale target plan. `MAX_SNAPSHOT_QUESTIONS` defaults to 100,000; the current snapshot implementation requires profiling and a relational analytics migration before larger deployments.

## Data interpretation

Question creation time determines activity. Answers do not increase question counts. A question may belong to several topics, but counts once within any topic and once in combined unique totals. Comparison windows have equal durations in UTC. A zero baseline with observed activity is Newly active; incomplete coverage is Insufficient history, never infinite growth.

The app uses an atomic published dataset for a consistent view of metrics and evidence. Original source text is untrusted and is rendered as plain text. Source author/license information accompanies available excerpts. Topic labels and classifications are application-generated interpretations, not source endorsements.

See the in-app About & Methodology page for definitions and known data limitations. Classification quality, provider quotas, source access, and deployment capacity should be evaluated on the intended production corpus before claiming the quality and performance targets in the technical plan.
