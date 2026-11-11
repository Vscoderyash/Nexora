"""Deterministic, provider-independent extraction and retrieval primitives."""
from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.skip:
            return
        clean = data.strip()
        if clean:
            self.parts.append(clean)
            if self.in_title:
                self.title_parts.append(clean)


def extract(content: str, content_type: str = "") -> tuple[str, str]:
    if "html" in content_type.lower() or re.search(r"<\s*(html|body|article|main|title)\b", content[:500], re.I):
        parser = TextExtractor()
        try:
            parser.feed(content)
            title = " ".join(parser.title_parts).strip()
            content = " ".join(parser.parts)
        except Exception:
            title = ""
    else:
        title = ""
    content = html.unescape(content).replace("\x00", " ")
    content = re.sub(r"\s+", " ", content).strip()
    return title[:500], content


def fingerprint(content: str) -> str:
    normalized = re.sub(r"\W+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8", "ignore")).hexdigest()


TAXONOMY = {
    "Artificial Intelligence": {"ai", "artificial intelligence", "machine learning", "neural", "llm"},
    "Technology": {"technology", "software", "computer", "digital", "platform"},
    "Science": {"science", "research", "study", "experiment", "scientific"},
    "Business": {"business", "company", "revenue", "market", "startup"},
    "Environment": {"climate", "environment", "energy", "emissions", "sustainability"},
    "Medicine and public health": {"health", "medicine", "patient", "clinical", "disease"},
    "Politics and government": {"government", "policy", "election", "minister", "regulation"},
}


def classify(title: str, content: str) -> list[str]:
    text = f"{title} {content}".lower()
    ranked = [(sum(1 for term in terms if term in text), category) for category, terms in TAXONOMY.items()]
    categories = [category for count, category in sorted(ranked, reverse=True) if count]
    return categories[:4] or ["Other"]


def short_summary(content: str, limit: int = 420) -> str:
    content = re.sub(r"\s+", " ", content).strip()
    if len(content) <= limit:
        return content
    end = content.rfind(".", 0, limit)
    return content[:end + 1] if end > limit // 2 else content[:max(0, limit - 1)].rstrip() + "…"


def relevant_passage(content: str, query: str, limit: int = 1600) -> str:
    """Return a bounded evidence passage near a query term."""
    text = " ".join((content or "").split())
    terms = re.findall(r"[\w-]{2,}", query.lower())
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms]
    positions = [position for position in positions if position >= 0]
    if not positions or len(text) <= limit:
        return text[:limit]
    center = min(positions)
    start = max(0, center - limit // 4)
    end = min(len(text), start + limit)
    passage = text[start:end]
    return ("…" if start else "") + passage + ("…" if end < len(text) else "")


def rank_documents(query: str, docs: list, max_results: int = 10) -> list[tuple[object, float]]:
    terms = {w for w in re.findall(r"[\w-]{2,}", query.lower()) if w not in {"what", "when", "where", "which", "that", "this", "with", "from", "about", "have"}}
    ranked = []
    for doc in docs:
        haystack = f"{doc.title} {doc.summary} {doc.content} {' '.join(doc.categories or [])}".lower()
        counts = Counter(re.findall(r"[\w-]{2,}", haystack))
        overlap = sum(min(counts.get(term, 0), 3) for term in terms)
        if not overlap:
            continue
        score = min(0.99, 0.25 + 0.12 * overlap / max(1, len(terms)) + 0.05 * min(overlap, 5))
        ranked.append((doc, round(score, 3)))
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)[:max_results]
