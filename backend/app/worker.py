"""Independent polling worker for configured RSS and Atom feeds."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import feedparser
from sqlalchemy import select

from .main import fetch_public, store_document
from .storage import CollectionJob, SessionLocal, Source, init_db, now_utc


async def run_once() -> int:
    completed = 0
    with SessionLocal() as db:
        # Interrupted work is made visible and can be retried on the next poll.
        interrupted = db.scalars(select(CollectionJob).where(CollectionJob.state == "running")).all()
        for job in interrupted:
            job.state = "failed"
            job.error = "Worker restarted while job was active"
            job.finished_at = now_utc()
        db.commit()
        sources = db.scalars(select(Source).where(Source.enabled.is_(True), Source.kind == "rss")).all()
        for source in sources:
            last_checked = source.last_checked_at
            if last_checked and last_checked.tzinfo is None:
                from datetime import timezone
                last_checked = last_checked.replace(tzinfo=timezone.utc)
            if last_checked and (now_utc() - last_checked).total_seconds() < source.interval_seconds:
                continue
            job = CollectionJob(id=str(uuid.uuid4()), source_id=source.id, state="running", discovered=0, collected=0)
            source.last_checked_at = now_utc()
            db.add(job)
            db.commit()
            try:
                raw, content_type = await fetch_public(source.url)
                feed = feedparser.parse(raw)
                if not feed.entries:
                    raise ValueError("Feed contains no entries or has an unsupported format")
                job.discovered = min(len(feed.entries), 200)
                for item in feed.entries[:200]:
                    url = str(item.get("link", ""))[:2048]
                    if not url.startswith(("http://", "https://")):
                        continue
                    title = str(item.get("title", "Untitled"))[:500]
                    summary = str(item.get("summary", item.get("description", "")))[:100000]
                    content = summary
                    if len(summary) < 120:
                        try:
                            page, page_type = await fetch_public(url)
                            # Prefer the actual article where it is permitted and reachable.
                            from .engine import extract
                            page_title, page_text = extract(page, page_type)
                            if len(page_text) > len(summary):
                                title = page_title or title
                                content = page_text
                        except Exception:
                            pass  # Feed-provided text remains usable evidence with its feed URL.
                    published = None
                    try:
                        parsed = item.get("published_parsed") or item.get("updated_parsed")
                        if parsed:
                            from datetime import datetime, timezone
                            published = datetime(*parsed[:6], tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        published = None
                    try:
                        _, changed = store_document(db, source, url, title, content, str(feed.feed.get("title", source.name)), published)
                        job.collected += int(changed)
                    except ValueError:
                        continue
                job.state = "completed"
                job.finished_at = now_utc()
                source.last_success_at = now_utc()
                source.failure_count = 0
                completed += 1
            except Exception as exc:
                job.state = "failed"
                job.error = str(exc)[:500]
                job.finished_at = now_utc()
                source.failure_count += 1
            db.commit()
    return completed


async def main():
    init_db()
    interval = max(30, int(os.getenv("NEXORA_WORKER_INTERVAL_SECONDS", "300")))
    while True:
        try:
            await run_once()
        except Exception as exc:
            print(f"collector cycle failed: {exc}", flush=True)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
