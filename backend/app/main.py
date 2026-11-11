from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .engine import classify, extract, extract_entities, fingerprint, rank_documents, relevant_passage, short_summary
from .crossref import search as crossref_search
from .storage import ApiKey, CollectionJob, Document, DocumentEntity, DocumentVersion, Entity, RequestLog, SessionLocal, Source, init_db, now_utc

APP_NAME = os.getenv("NEXORA_NAME", "NEXORA")
MAX_BYTES = int(os.getenv("NEXORA_MAX_FETCH_BYTES", "2000000"))
FETCH_TIMEOUT = float(os.getenv("NEXORA_FETCH_TIMEOUT_SECONDS", "12"))
RATE_LIMIT = int(os.getenv("NEXORA_REQUESTS_PER_MINUTE", "120"))
RATE_WINDOW: dict[str, list[float]] = {}
app = FastAPI(title=f"{APP_NAME} Knowledge API", version="1.0.0", description="Private, source-backed knowledge retrieval API.")

origins = [x.strip() for x in os.getenv("NEXORA_CORS_ORIGINS", "http://localhost:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.on_event("startup")
def startup():
    init_db()
    bootstrap = os.getenv("NEXORA_BOOTSTRAP_KEY", "").strip()
    if bootstrap:
        digest = hashlib.sha256(bootstrap.encode()).hexdigest()
        with SessionLocal() as db:
            if not db.scalar(select(ApiKey).where(ApiKey.key_hash == digest)):
                db.add(ApiKey(id=str(uuid.uuid4()), name="bootstrap", key_hash=digest, permissions=["admin", "read", "write"]))
                db.commit()


@app.middleware("http")
async def request_audit(request: Request, call_next):
    started = time.perf_counter()
    request.state.key_id = None
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse(status_code=500, content={"detail": "Internal service error"})
    if request.url.path.startswith("/v1/"):
        try:
            with SessionLocal() as db:
                db.add(RequestLog(id=str(uuid.uuid4()), key_id=request.state.key_id, method=request.method, path=request.url.path[:300], status_code=response.status_code, latency_ms=(time.perf_counter() - started) * 1000))
                db.commit()
        except Exception:
            pass
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def authenticated(request: Request, authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer API key required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:].strip()
    if len(token) < 24 or len(token) > 512:
        raise HTTPException(401, "Invalid API key")
    digest = hashlib.sha256(token.encode()).hexdigest()
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.active.is_(True)))
    if not key:
        raise HTTPException(401, "Invalid or revoked API key")
    now = time.time()
    window = [stamp for stamp in RATE_WINDOW.get(key.id, []) if now - stamp < 60]
    if len(window) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit exceeded")
    window.append(now)
    RATE_WINDOW[key.id] = window
    key.last_used_at = now_utc()
    key.request_count += 1
    db.commit()
    request.state.key_id = key.id
    return key


def require(permission: str):
    def dependency(key: ApiKey = Depends(authenticated)):
        if permission not in (key.permissions or []) and "admin" not in (key.permissions or []):
            raise HTTPException(403, f"The API key lacks the {permission} permission")
        return key
    return dependency


class QueryInput(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    search_mode: str = Field(default="hybrid", pattern="^(keyword|hybrid|semantic)$")
    max_results: int = Field(default=10, ge=1, le=50)
    include_sources: bool = True
    include_metadata: bool = True
    freshness: str = Field(default="prefer_recent", pattern="^(any|prefer_recent|current_only)$")


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    url: HttpUrl
    kind: str = Field(default="rss", pattern="^(rss|website)$")
    license_notes: str = Field(default="", max_length=2000)
    interval_seconds: int = Field(default=3600, ge=300, le=604800)
    enabled: bool = True


class CollectInput(BaseModel):
    url: HttpUrl
    source_name: str = Field(default="Manual research", max_length=240)
    license_notes: str = Field(default="User-authorized public source", max_length=2000)


class ResearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    max_results: int = Field(default=5, ge=1, le=10)


class KeyInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    permissions: list[str] = Field(default_factory=lambda: ["read"])

    @field_validator("permissions")
    @classmethod
    def valid_permissions(cls, value):
        if not value or set(value) - {"read", "write", "admin"}:
            raise ValueError("Permissions must be read, write, and/or admin")
        return list(dict.fromkeys(value))


def safe_public_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Only public HTTP and HTTPS URLs are allowed")
    host = parsed.hostname.rstrip(".").lower()
    blocked_hosts = {"localhost", "metadata.google.internal"}
    if host in blocked_hosts or host.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(400, "Private and local hosts cannot be collected")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        if not addresses or any(not addr.is_global for addr in addresses):
            raise HTTPException(400, "Private and non-routable hosts cannot be collected")
    except socket.gaierror:
        raise HTTPException(400, "Source host could not be resolved")
    except ValueError:
        pass
    return raw_url


async def fetch_public(url: str) -> tuple[str, str]:
    safe_public_url(url)
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    headers = {"User-Agent": f"{APP_NAME}/1.0 (+permitted research collector)"}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False, headers=headers) as client:
        try:
            robots = await client.get(robots_url)
            if robots.status_code in {401, 403} or robots.status_code >= 500:
                raise HTTPException(403, "Robots policy is unavailable or blocks collection")
            if robots.status_code == 200:
                from urllib.robotparser import RobotFileParser
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(robots.text.splitlines())
                if not parser.can_fetch(headers["User-Agent"], url):
                    raise HTTPException(403, "Robots policy disallows collection")
        except HTTPException:
            raise
        except httpx.HTTPError:
            pass
        try:
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    raise HTTPException(400, "Redirects are not followed. Submit the final public URL directly.")
                response.raise_for_status()
                declared_size = int(response.headers.get("content-length", "0") or 0)
                if declared_size > MAX_BYTES:
                    raise HTTPException(413, "Source exceeds the configured content size limit")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "Source exceeds the configured content size limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), response.headers.get("content-type", "")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, f"Source returned HTTP {exc.response.status_code}")
        except httpx.HTTPError:
            raise HTTPException(502, "Source could not be fetched within the configured timeout")


def json_doc(doc: Document, score: float | None = None, include_content: bool = True) -> dict:
    out = {"document_id": doc.id, "title": doc.title, "summary": doc.summary, "source_url": doc.url,
           "publisher": doc.publisher, "published_at": doc.published_at.isoformat() if doc.published_at else None,
           "collected_at": doc.collected_at.isoformat(), "verified_at": doc.verified_at.isoformat() if doc.verified_at else None,
           "categories": doc.categories or [], "quality": doc.quality or {}, "status": doc.status}
    if include_content:
        out["content"] = doc.content
    if score is not None:
        out["relevance_score"] = score
    return out


def store_document(db: Session, source: Source, url: str, title: str, content: str, publisher: str = "", published=None) -> tuple[Document, bool]:
    clean_title, text = extract(content, "text/html")
    title = title or clean_title or url
    text = text[:MAX_BYTES]
    if len(text) < 40:
        raise ValueError("Extracted content is too short to store")
    fp = fingerprint(text)
    existing = db.scalar(select(Document).where(Document.url == url))
    if existing:
        if existing.fingerprint == fp:
            return existing, False
        db.add(DocumentVersion(id=str(uuid.uuid4()), document_id=existing.id, version=existing.version, title=existing.title, content=existing.content, fingerprint=existing.fingerprint))
        existing.version += 1
        existing.fingerprint = fp
        existing.title = title[:500]
        existing.content = text
        existing.summary = short_summary(text)
        existing.categories = classify(title, text)
        existing.processed_at = now_utc()
        existing.verified_at = now_utc()
        existing.status = "processed"
        existing.duplicate_of = None
        index_entities(db, existing)
        db.flush()
        return existing, True
    duplicate = db.scalar(select(Document).where(Document.fingerprint == fp))
    if duplicate:
        doc = Document(id=str(uuid.uuid4()), source_id=source.id, url=url, title=title[:500], publisher=publisher[:300],
                       published_at=published, collected_at=now_utc(), processed_at=now_utc(), verified_at=now_utc(), content=text,
                       summary=short_summary(text), fingerprint=fp, categories=classify(title, text),
                       quality={"extraction": "complete", "duplicate": "exact-content", "evidence": "source URL retained"},
                       status="duplicate", duplicate_of=duplicate.id)
        db.add(doc)
        db.flush()
        return doc, True
    doc = Document(id=str(uuid.uuid4()), source_id=source.id, url=url, title=title[:500], publisher=publisher[:300],
                   published_at=published, collected_at=now_utc(), processed_at=now_utc(), verified_at=now_utc(), content=text,
                   summary=short_summary(text), fingerprint=fp, categories=classify(title, text),
                   quality={"extraction": "complete", "source_reliability": "unrated", "evidence": "source URL retained"}, status="processed")
    db.add(doc)
    db.flush()
    index_entities(db, doc)
    return doc, True


def index_entities(db: Session, doc: Document) -> None:
    db.query(DocumentEntity).filter(DocumentEntity.document_id == doc.id).delete(synchronize_session=False)
    for candidate in extract_entities(f"{doc.title}\n{doc.content}"):
        normalized = candidate["name"].casefold()
        entity = db.scalar(select(Entity).where(Entity.normalized_name == normalized, Entity.kind == candidate["kind"]))
        if entity is None:
            entity = Entity(id=str(uuid.uuid4()), name=candidate["name"], normalized_name=normalized, kind=candidate["kind"])
            db.add(entity)
            db.flush()
        db.add(DocumentEntity(document_id=doc.id, entity_id=entity.id, confidence=candidate["confidence"], evidence=candidate["evidence"][:400]))


@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "alive"}


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "index.html")))


@app.get("/v1/system/status")
def system_status(db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    db.execute(select(1))
    return {"name": APP_NAME, "status": "operational", "database": "connected", "storage": "sqlite" if str(db.bind.url).startswith("sqlite") else "postgresql",
            "workers": {"rss_collector": "separate process; run python -m app.worker", "redis_queue": "not configured"}, "features": {"keyword_search": "available", "entity_graph": "available; rule-based candidates", "semantic_search": "not configured", "answer_generation": "not configured"}, "checked_at": now_utc().isoformat()}


@app.get("/v1/admin/overview")
def overview(db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    docs = db.scalar(select(func.count()).select_from(Document)) or 0
    processed = db.scalar(select(func.count()).select_from(Document).where(Document.status == "processed")) or 0
    sources = db.scalar(select(func.count()).select_from(Source)) or 0
    today = now_utc() - timedelta(hours=24)
    collected = db.scalar(select(func.count()).select_from(Document).where(Document.collected_at >= today)) or 0
    requests = db.scalar(select(func.count()).select_from(RequestLog)) or 0
    average = db.scalar(select(func.avg(RequestLog.latency_ms)))
    failed = db.scalar(select(func.count()).select_from(CollectionJob).where(CollectionJob.state == "failed")) or 0
    duplicates = db.scalar(select(func.count()).select_from(Document).where(Document.status == "duplicate")) or 0
    recent = db.scalars(select(Document).order_by(Document.collected_at.desc()).limit(6)).all()
    enabled = db.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True))) or 0
    return {"documents": docs, "processed_documents": processed, "indexed_documents": processed, "sources": sources,
            "collected_last_24h": collected, "awaiting_processing": 0, "failed_jobs": failed,
            "duplicate_documents": duplicates, "api_requests": requests, "avg_retrieval_latency_ms": round(average, 1) if average else None,
            "worker_status": "configured" if enabled else "waiting for sources", "recent_documents": [json_doc(x, include_content=False) for x in recent]}


@app.get("/v1/admin/sources")
def list_sources(db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    rows = db.scalars(select(Source).order_by(Source.created_at.desc())).all()
    return [{"id": s.id, "name": s.name, "url": s.url, "kind": s.kind, "enabled": s.enabled, "license_notes": s.license_notes,
             "interval_seconds": s.interval_seconds, "failure_count": s.failure_count,
             "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
             "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
             "documents": db.scalar(select(func.count()).select_from(Document).where(Document.source_id == s.id)) or 0} for s in rows]


@app.post("/v1/admin/sources", status_code=201)
def create_source(data: SourceInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("write"))):
    url = safe_public_url(str(data.url))
    row = Source(id=str(uuid.uuid4()), name=data.name, url=url, kind=data.kind, license_notes=data.license_notes,
                 interval_seconds=data.interval_seconds, enabled=data.enabled)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A source with this URL already exists")
    return {"id": row.id, "name": row.name, "url": row.url, "kind": row.kind, "enabled": row.enabled}


@app.patch("/v1/admin/sources/{source_id}")
def update_source(source_id: str, enabled: bool, db: Session = Depends(get_db), _: ApiKey = Depends(require("write"))):
    row = db.get(Source, source_id)
    if not row:
        raise HTTPException(404, "Source not found")
    row.enabled = enabled
    db.commit()
    return {"id": row.id, "enabled": row.enabled}


@app.get("/v1/admin/documents")
def admin_documents(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    query = select(Document).order_by(Document.collected_at.desc())
    if q:
        needle = f"%{q[:200]}%"
        query = query.where(Document.title.ilike(needle) | Document.content.ilike(needle) | Document.url.ilike(needle))
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {"results": [json_doc(d, include_content=False) for d in rows], "page": page, "page_size": page_size}


@app.get("/v1/admin/jobs")
def admin_jobs(db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    rows = db.scalars(select(CollectionJob).order_by(CollectionJob.started_at.desc()).limit(50)).all()
    return [{"id": j.id, "source_id": j.source_id, "state": j.state, "discovered": j.discovered, "collected": j.collected, "error": j.error, "started_at": j.started_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in rows]


@app.post("/v1/admin/collect")
async def collect_one(data: CollectInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("write"))):
    url = safe_public_url(str(data.url))
    source = db.scalar(select(Source).where(Source.url == url))
    if not source:
        source = Source(id=str(uuid.uuid4()), name=data.source_name, url=url, kind="website", license_notes=data.license_notes, interval_seconds=86400, enabled=False)
        db.add(source)
        db.flush()
    job = CollectionJob(id=str(uuid.uuid4()), source_id=source.id, state="running", discovered=1, collected=0)
    db.add(job)
    db.commit()
    try:
        raw, content_type = await fetch_public(url)
        title, text = extract(raw, content_type)
        doc, changed = store_document(db, source, url, title or data.source_name, text, urlparse(url).hostname or "")
        job.state = "completed"
        job.collected = int(changed)
        source.last_success_at = source.last_checked_at = now_utc()
        source.failure_count = 0
        db.commit()
        return {"job_id": job.id, "state": job.state, "stored": changed, "document": json_doc(doc, include_content=False)}
    except Exception as exc:
        db.rollback()
        failed = db.get(CollectionJob, job.id)
        if failed:
            failed.state = "failed"
            failed.error = str(exc)[:500]
            failed.finished_at = now_utc()
            db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "Collection failed; inspect the job log")


def search_payload(data: QueryInput, db: Session):
    terms = list(dict.fromkeys(re.findall(r"[\w-]{2,}", data.query.lower())))[:12]
    if not terms:
        candidates = []
    else:
        conditions = []
        for term in terms:
            pattern = f"%{term}%"
            conditions.extend((Document.title.ilike(pattern), Document.summary.ilike(pattern), Document.content.ilike(pattern)))
        # Bound candidate materialization so large corpora do not load every document into API memory.
        candidates = db.execute(select(Document.id, Document.title, Document.summary,
                                       func.substr(Document.content, 1, 5000).label("content"), Document.categories)
                                .where(Document.status == "processed", or_(*conditions))
                                .order_by(Document.collected_at.desc()).limit(500)).all()
    ranked = rank_documents(data.query, candidates, data.max_results)
    full_documents = {doc.id: doc for doc in db.scalars(select(Document).where(Document.id.in_([doc.id for doc, _ in ranked]))).all()} if ranked else {}
    results = []
    for candidate, score in ranked:
        doc = full_documents.get(candidate.id)
        if doc:
            result = json_doc(doc, score, include_content=False)
            result["content"] = relevant_passage(doc.content, data.query)
            results.append(result)
    for result in results:
        if not data.include_sources:
            result.pop("source_url", None)
            result.pop("publisher", None)
        if not data.include_metadata:
            for key in ("published_at", "collected_at", "verified_at", "categories", "quality", "status"):
                result.pop(key, None)
    return {"success": True, "request_id": f"req_{uuid.uuid4().hex}", "query": data.query,
            "results": results,
            "metadata": {"retrieval_mode": "keyword" if data.search_mode == "semantic" else data.search_mode,
                         "results_count": len(ranked), "freshness": "stored", "knowledge_version": "1.0",
                         "semantic_search": "not configured" if data.search_mode in {"hybrid", "semantic"} else None}}


@app.post("/v1/knowledge/query")
def query_knowledge(data: QueryInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    return search_payload(data, db)


@app.post("/v1/knowledge/search")
def search_knowledge(data: QueryInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    return search_payload(data, db)


@app.get("/v1/knowledge/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    result = json_doc(doc)
    result["history"] = [{"version": row.version, "title": row.title, "fingerprint": row.fingerprint,
                          "captured_at": row.captured_at.isoformat(), "content": row.content}
                         for row in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version.desc())).all()]
    result["duplicate_of"] = doc.duplicate_of
    return result


@app.get("/v1/knowledge/entities/{entity_id}")
def get_entity(entity_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    rows = db.execute(select(Document, DocumentEntity).join(DocumentEntity, Document.id == DocumentEntity.document_id)
                      .where(DocumentEntity.entity_id == entity.id).order_by(Document.collected_at.desc()).limit(100)).all()
    return {"id": entity.id, "name": entity.name, "kind": entity.kind,
            "documents": [{**json_doc(doc, include_content=False), "mention_confidence": mention.confidence, "evidence": mention.evidence} for doc, mention in rows]}


@app.get("/v1/admin/entities")
def list_entities(q: str = "", limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    query = select(Entity).order_by(Entity.name).limit(limit)
    if q:
        query = select(Entity).where(Entity.name.ilike(f"%{q[:120]}%"),).order_by(Entity.name).limit(limit)
    entities = db.scalars(query).all()
    return [{"id": entity.id, "name": entity.name, "kind": entity.kind,
             "document_count": db.scalar(select(func.count()).select_from(DocumentEntity).where(DocumentEntity.entity_id == entity.id)) or 0}
            for entity in entities]


@app.post("/v1/admin/entities/reindex")
def reindex_entities(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db), _: ApiKey = Depends(require("write"))):
    docs = db.scalars(select(Document).where(Document.status == "processed").order_by(Document.collected_at.desc()).offset(offset).limit(limit)).all()
    for doc in docs:
        index_entities(db, doc)
    db.commit()
    mentions = db.scalar(select(func.count()).select_from(DocumentEntity).where(DocumentEntity.document_id.in_([d.id for d in docs]))) if docs else 0
    total = db.scalar(select(func.count()).select_from(Document).where(Document.status == "processed")) or 0
    return {"processed_documents": len(docs), "entity_mentions": mentions or 0, "next_offset": offset + len(docs) if offset + len(docs) < total else None, "documents_total": total}


@app.get("/v1/admin/graph")
def graph_data(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    mentions = db.execute(select(DocumentEntity, Entity, Document)
                           .join(Entity, Entity.id == DocumentEntity.entity_id)
                           .join(Document, Document.id == DocumentEntity.document_id)
                           .order_by(Document.collected_at.desc()).limit(500)).all()
    entities, documents, edges = {}, {}, []
    for mention, entity, doc in mentions:
        entities[entity.id] = {"id": entity.id, "label": entity.name, "kind": entity.kind, "type": "entity"}
        documents[doc.id] = {"id": doc.id, "label": doc.title, "kind": "document", "type": "document", "source_url": doc.url}
        edges.append({"source": entity.id, "target": doc.id, "confidence": mention.confidence, "evidence": mention.evidence})
    nodes = list(entities.values())[:limit] + list(documents.values())[:limit]
    visible = {node["id"] for node in nodes}
    edges = [edge for edge in edges if edge["source"] in visible and edge["target"] in visible]
    return {"nodes": nodes, "edges": edges}


@app.get("/v1/knowledge/sources/{source_id}")
def get_source(source_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    src = db.get(Source, source_id)
    if not src:
        raise HTTPException(404, "Source not found")
    docs = db.scalars(select(Document).where(Document.source_id == src.id).order_by(Document.collected_at.desc()).limit(30)).all()
    jobs = db.scalars(select(CollectionJob).where(CollectionJob.source_id == src.id).order_by(CollectionJob.started_at.desc()).limit(30)).all()
    return {"id": src.id, "name": src.name, "url": src.url, "kind": src.kind, "enabled": src.enabled, "license_notes": src.license_notes,
            "failure_count": src.failure_count, "last_success_at": src.last_success_at.isoformat() if src.last_success_at else None,
            "documents": [json_doc(d, include_content=False) for d in docs], "history": [{"state": j.state, "started_at": j.started_at.isoformat(), "error": j.error} for j in jobs]}


@app.post("/v1/knowledge/answer")
def answer(data: QueryInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("read"))):
    evidence = search_payload(data, db)
    return {"success": True, "generated": False, "reason": "No answer generation provider is configured.", "query": data.query,
            "answer": None, "evidence": evidence["results"], "metadata": evidence["metadata"]}


@app.post("/v1/knowledge/research")
async def research(data: ResearchInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("write"))):
    source = db.scalar(select(Source).where(Source.url == "https://api.crossref.org/works"))
    if not source:
        source = Source(id=str(uuid.uuid4()), name="Crossref metadata API", url="https://api.crossref.org/works", kind="research",
                        enabled=False, license_notes="Public scholarly metadata API; item licenses and full text rights vary.", interval_seconds=86400)
        db.add(source)
        db.flush()
    job = CollectionJob(id=str(uuid.uuid4()), source_id=source.id, state="running", discovered=0, collected=0)
    db.add(job)
    db.commit()
    try:
        records = await crossref_search(data.query, data.max_results, FETCH_TIMEOUT)
        job.discovered = len(records)
        documents = []
        for record in records:
            doc, changed = store_document(db, source, record["url"], record["title"], record["content"], record["publisher"], record["published_at"])
            job.collected += int(changed)
            documents.append(doc)
        job.state = "completed"
        job.finished_at = now_utc()
        source.last_checked_at = source.last_success_at = now_utc()
        source.failure_count = 0
        db.commit()
        return {"success": True, "request_id": f"req_{uuid.uuid4().hex}", "query": data.query,
                "provider": "Crossref public metadata", "results": [json_doc(doc, include_content=True) for doc in documents],
                "metadata": {"discovered": len(records), "stored_or_updated": job.collected, "full_text": False,
                             "source_content": "bibliographic metadata and abstracts where supplied by the publisher"}}
    except Exception as exc:
        db.rollback()
        failed = db.get(CollectionJob, job.id)
        if failed:
            failed.state = "failed"
            failed.error = str(exc)[:500]
            failed.finished_at = now_utc()
            source_row = db.get(Source, source.id)
            if source_row:
                source_row.failure_count += 1
            db.commit()
        raise HTTPException(502, "Bounded research failed; inspect the collection job log")


@app.get("/v1/account/usage")
def usage(key: ApiKey = Depends(authenticated), db: Session = Depends(get_db)):
    recent = db.scalar(select(func.count()).select_from(RequestLog).where(RequestLog.key_id == key.id, RequestLog.created_at >= now_utc() - timedelta(days=30))) or 0
    return {"key_id": key.id, "name": key.name, "permissions": key.permissions, "requests_total": key.request_count, "requests_last_30_days": recent,
            "rate_limit_per_minute": RATE_LIMIT, "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None}


@app.get("/v1/admin/keys")
def list_keys(db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    rows = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return [{"id": k.id, "name": k.name, "permissions": k.permissions, "active": k.active, "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None, "request_count": k.request_count} for k in rows]


@app.post("/v1/admin/keys", status_code=201)
def create_key(data: KeyInput, db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    token = "nxr_" + secrets.token_urlsafe(36)
    key = ApiKey(id=str(uuid.uuid4()), name=data.name, key_hash=hashlib.sha256(token.encode()).hexdigest(), permissions=data.permissions)
    db.add(key)
    db.commit()
    return {"id": key.id, "name": key.name, "permissions": key.permissions, "secret": token, "shown_once": True}


@app.delete("/v1/admin/keys/{key_id}")
def revoke_key(key_id: str, db: Session = Depends(get_db), current: ApiKey = Depends(require("admin"))):
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API key not found")
    if key.id == current.id:
        raise HTTPException(400, "You cannot revoke the key used for this request")
    key.active = False
    db.commit()
    return {"id": key.id, "active": False}


@app.get("/v1/admin/monitoring")
def monitoring(db: Session = Depends(get_db), _: ApiKey = Depends(require("admin"))):
    avg = db.scalar(select(func.avg(RequestLog.latency_ms)))
    count = db.scalar(select(func.count()).select_from(RequestLog)) or 0
    return {"api_requests": count, "average_latency_ms": round(avg, 1) if avg else None, "database": "connected", "semantic_index": "not configured", "queue": "not configured"}


def private_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http", "scheme": "bearer", "bearerFormat": "NEXORA API key"
    }
    for path, operations in schema.get("paths", {}).items():
        if path.startswith("/v1/"):
            for operation in operations.values():
                if isinstance(operation, dict):
                    operation["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = private_openapi
