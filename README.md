# NEXORA — Global Knowledge Intelligence Engine

NEXORA is a private, source-backed knowledge service. This repository starts with a usable single-node foundation: a FastAPI service, persistent relational storage, hashed API keys, source/document management, an RSS worker, deterministic extraction and cleaning, keyword retrieval, and a connected admin console. The modules are deliberately small so PostgreSQL, Redis-backed queues, vector search, and provider adapters can be added without changing the public API contract.

## Start locally

Requires Python 3.11 or newer.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item ..\.env.example ..\.env
python -m app.cli create-key --name local-admin --permissions admin,read,write
```

Save the displayed token; it is already stored as a hash in the local database and cannot be shown again. Then run:

```powershell
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the admin console and http://localhost:8000/docs for the private API reference. Provide the key in the console's connection panel. Keys are displayed once and only SHA-256 hashes are stored. SQLite is the zero-setup default. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for a shared deployment. In Docker Compose, the default database file lives in the persistent `nexora-data` volume.

`NEXORA_BOOTSTRAP_KEY` is an optional deployment setting for pre-provisioning a key from a secret manager. For a local first run, use the CLI command above.

For Docker Compose, create the persistent-database key and start both services:

```powershell
Copy-Item .env.example .env
docker compose build
docker compose run --rm api python -m app.cli create-key --name local-admin --permissions admin,read,write
docker compose up -d
```

## Run the collector

Start the API and collector as separate processes:

```powershell
cd backend
python -m app.worker
```

Add an RSS source in the Sources panel or through `POST /v1/admin/sources`. The worker checks enabled RSS/Atom sources on the configured interval, applies per-source throttling, stores changed items, and processes each item. Website collection is intentionally an explicit, bounded operation (`POST /v1/admin/collect`) with a timeout, content-size cap, and robots.txt check. Configure only sources whose terms and licenses permit collection.

## API examples

All `/v1` routes require `Authorization: Bearer <key>`.

```http
POST /v1/knowledge/query
Content-Type: application/json
Authorization: Bearer <key>

{"query":"What is ...?","search_mode":"hybrid","max_results":10,"include_sources":true}
```

The service returns passages and source metadata, not invented answers. `/v1/knowledge/answer` clearly reports that generation is unconfigured until an administrator installs and configures a provider adapter. `/v1/knowledge/research` accepts a question and up to ten results, uses Crossref's public bibliographic metadata API, and stores DOI-linked records and publisher abstracts when supplied. It does not claim to download publisher full text.

## Configuration

See [.env.example](.env.example). Never commit `.env` or API keys. Use TLS at the reverse proxy in deployed environments. Restrict CORS and bind the service to a trusted network. The development console uses the same bearer key as the API.

## Architecture and current boundaries

- `backend/app/storage.py`: relational records and database session boundary (SQLite by default; PostgreSQL supported through `DATABASE_URL`).
- `backend/app/engine.py`: extraction, fingerprinting, conservative classification, and ranked keyword retrieval.
- `backend/app/main.py`: versioned private API and admin endpoints.
- `backend/app/worker.py`: independent restart-safe polling worker for RSS sources.
- `index.html`: root-level GitHub Pages entry page; the API serves this same file and reads counts from backend endpoints.
- `docker-compose.yml`: API and persistent volume for a local container deployment.

Semantic vectors, provider-backed summaries, key rotation UI, Redis/Celery queues, object storage, graph visualization, and production migrations are planned extensions; the current interface reports optional integrations as unavailable instead of simulating them. The database schema is created at startup for local development. Production deployments should add Alembic migrations, managed PostgreSQL backups, TLS, secret management, and a durable queue before scaling collection.

## GitHub Pages

The dashboard entry point is at the repository root as `index.html`. Enable GitHub Pages for the `main` branch and repository root. The static page can be hosted separately from the API: set `NEXORA_CORS_ORIGINS` on the API server to the exact Pages origin (typically `https://vscoderyash.github.io`) and enter the API server URL plus a private API key in the console connection panel. Serve the API over HTTPS and issue a read-only key for Pages use; admin operations require an admin key. The page keeps the key for the current browser tab only.

## Focused checks

From `backend/`, run `python -m unittest discover -s tests -v` for extraction, cleaning, fingerprinting, classification, summary bounds, and retrieval ranking checks.
