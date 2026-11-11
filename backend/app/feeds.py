"""Small RSS 2.0 and Atom parser using Python's standard XML library."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree


def _tag(element, name: str):
    return next((child for child in list(element) if child.tag.rsplit("}", 1)[-1].lower() == name.lower()), None)


def _text(element, name: str) -> str:
    child = _tag(element, name)
    if child is None:
        return ""
    return " ".join("".join(child.itertext()).split())


def _published(value: str):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed(raw: str) -> dict:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("Feed is not valid RSS or Atom XML") from exc
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name == "rss":
        channel = _tag(root, "channel") or root
        title = _text(channel, "title")
        entries = _tag(channel, "item")
        items = [child for child in list(channel) if child.tag.rsplit("}", 1)[-1].lower() == "item"]
        return {"title": title, "entries": [_rss_entry(item) for item in items]}
    if root_name == "feed":
        title = _text(root, "title")
        items = [child for child in list(root) if child.tag.rsplit("}", 1)[-1].lower() == "entry"]
        return {"title": title, "entries": [_atom_entry(item) for item in items]}
    raise ValueError("Feed must use RSS 2.0 or Atom")


def _rss_entry(item):
    published = _text(item, "pubDate") or _text(item, "date")
    return {"title": _text(item, "title") or "Untitled", "link": _text(item, "link"),
            "summary": _text(item, "description") or _text(item, "encoded"), "published_at": _published(published)}


def _atom_entry(item):
    link = next((node.attrib.get("href", "") for node in list(item)
                 if node.tag.rsplit("}", 1)[-1].lower() == "link" and node.attrib.get("rel", "alternate") == "alternate"), "")
    published = _text(item, "published") or _text(item, "updated")
    return {"title": _text(item, "title") or "Untitled", "link": link,
            "summary": _text(item, "summary") or _text(item, "content"), "published_at": _published(published)}
