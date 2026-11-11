# Architecture and staged delivery

## First runnable slice

```text
Admin console ── bearer key ── FastAPI ── SQLAlchemy ── SQLite / PostgreSQL
                                      ├── source collection (bounded HTTP + robots.txt)
                                      ├── extraction, cleaning, fingerprints, categories
                                      ├── keyword retrieval with source metadata
                                      └── audit and usage records

Separate collector process ── RSS / Atom polling ── same relational database
```

The API contract is independent from the admin console. Collection uses a separate process and can continue while the console is closed. The initial database adapter uses portable SQLAlchemy tables; local development defaults to SQLite and deployments can point to PostgreSQL. Source bodies are stored only when retrieved under explicit source configuration and within a byte limit. Robots policy, content length, request timeout, URL scheme, and non-public host checks are enforced before website retrieval.

## Data records

- `sources`: connector type, enabled state, license notes, polling interval, health timestamps, and failure count.
- `documents`: canonical source URL, source relation, extracted text, summary, category labels, quality indicators, fingerprint, processing state, and duplicate provenance.
- `document_versions`: prior content snapshots for changed URLs.
- `collection_jobs`: durable job outcomes and recoverable failure details.
- `api_keys`: one-way key hash, permissions, request count, and last use.
- `request_logs`: route, status, latency, key reference, and timestamp.

## Security model

All versioned API and admin routes require a bearer key. Keys are generated with cryptographic randomness, hashed with SHA-256, permission checked, rate limited in the API process, and revocable. The secret is shown once. Internet pages are treated as data; no source text is executed or used as application instructions. External fetches reject local/private hosts, follow no redirects, observe robots.txt, enforce request timeout and response size, and never fetch from frontend code. Configure TLS at a trusted reverse proxy and keep `.env` out of source control.

## Delivery status

### In place

- Private FastAPI service, OpenAPI bearer scheme, API key create/list/revoke, usage view, rate limit, and audit records.
- Database-backed sources, documents, collection jobs, duplicate provenance, and historical content snapshots.
- Explicit one-URL public research and a separate polling RSS/Atom worker.
- HTML/text extraction, whitespace normalization, exact fingerprints, conservative multi-label taxonomy, short extractive summaries, keyword retrieval, and source citations.
- Responsive dark/light console with live database statistics, source controls, explorer, query playground, key management, collection history, and service status.
- Docker Compose single-node deployment, environment example, and focused processing tests.

### Next stages

1. Add Alembic migrations and production-grade PostgreSQL deployment/backup guidance.
2. Move polling and processing to Redis/Celery with bounded concurrency, exponential retry, and separate worker pools.
3. Add pluggable discovery connectors (research metadata, government datasets, licensed news/search APIs) and source health checks.
4. Add typed entities and relationships, model-backed enrichment adapters, vector embeddings, pgvector hybrid ranking, and index versioning.
5. Add configurable provider-secret storage, supported LLM adapters, citation validation, conflict reporting, and cost tracking.
6. Expand operational views, source correction/retraction, administrative authentication hardening, and production monitoring.

The current keyword retriever scans processed records in the relational database. It does not claim semantic retrieval, model-generated answers, entity graph support, continuous Google discovery, Redis queues, or object storage until those integrations are implemented and configured.
