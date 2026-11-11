"""Bounded adapter for Crossref's public works metadata API."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape

import httpx


API_URL = "https://api.crossref.org/works"
MAX_RESPONSE_BYTES = 2_000_000


def _date(item: dict):
    for field in ("published-online", "published-print", "issued"):
        parts = item.get(field, {}).get("date-parts") or []
        if parts and parts[0]:
            values = list(parts[0]) + [1, 1]
            try:
                return datetime(int(values[0]), int(values[1]), int(values[2]), tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _clean_markup(value: str) -> str:
    return unescape(re.sub(r"<[^>]*>", " ", value or "")).replace("\x00", " ").strip()


def normalize(item: dict) -> dict | None:
    doi = str(item.get("DOI") or "").strip()
    if not doi:
        return None
    titles = item.get("title") or []
    title = _clean_markup(str(titles[0])) if titles else doi
    authors = []
    for person in (item.get("author") or [])[:20]:
        name = " ".join(x for x in (person.get("given"), person.get("family")) if x)
        if name:
            authors.append(name)
    venues = item.get("container-title") or []
    venue = _clean_markup(str(venues[0])) if venues else ""
    abstract = _clean_markup(str(item.get("abstract") or ""))
    content = "\n".join(x for x in [
        f"Title: {title}", f"Authors: {', '.join(authors)}" if authors else "",
        f"Published in: {venue}" if venue else "", f"Abstract: {abstract}" if abstract else "",
        f"DOI: https://doi.org/{doi}",
    ] if x)
    return {"doi": doi, "url": f"https://doi.org/{doi}", "title": title[:500], "authors": authors,
            "publisher": str(item.get("publisher") or venue)[:300], "published_at": _date(item),
            "content": content, "license": item.get("license", [])}


async def search(query: str, limit: int = 5, timeout: float = 10.0) -> list[dict]:
    """Return at most ten normalized records. No full text is fetched or implied."""
    limit = max(1, min(int(limit), 10))
    headers = {"User-Agent": "NEXORA/1.0 (public metadata discovery)"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
        try:
            response = await client.get(API_URL, params={"query.bibliographic": query[:1000], "rows": limit})
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("Crossref did not respond before the research timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Crossref returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Crossref could not be reached") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Crossref response exceeded the configured size budget")
    try:
        body = response.json()
        items = body.get("message", {}).get("items", [])
    except (ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError("Crossref returned an invalid metadata response") from exc
    results = []
    seen = set()
    for item in items[:limit]:
        record = normalize(item)
        if record and record["doi"].lower() not in seen:
            seen.add(record["doi"].lower())
            results.append(record)
    return results
