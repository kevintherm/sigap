"""Keyword (BM25) retrieval over handbook sections, with a strict NOT_FOUND gate.

Stands in for the Astra DB vector search of the Langflow build. The gate matters more
than the ranking: when evidence is weak Sigap must say NOT_FOUND rather than stretch a
loosely related section into an answer.
"""
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from .data import Document, Section, documents
from .textutil import tokens

# A section counts as an answer only if the question hits enough of its curated tags,
# or one tag that belongs to this section alone and covers most of the question.
MIN_TAG_HITS = 2
MIN_SCORE = 4.0
MIN_COVERAGE_SINGLE_TAG = 0.5


@dataclass
class Hit:
    doc: Document
    section: Section
    score: float
    tag_hits: list[str]
    unique_tag_hits: list[str]
    coverage: float


@dataclass
class _Entry:
    doc: Document
    section: Section
    tf: Counter
    length: int
    tag_terms: set


@lru_cache
def _index():
    entries = []
    for doc in documents():
        for s in doc.sections:
            tag_terms = set(tokens(" ".join(s.tags)))
            # Tags are weighted x3: they are the staff-curated vocabulary for the section.
            terms = tokens(f"{s.title_id} {s.title_en} {s.text_id} {s.text_en}") + list(tag_terms) * 3
            entries.append(_Entry(doc, s, Counter(terms), len(terms), tag_terms))
    n = len(entries)
    df = Counter(t for e in entries for t in e.tf)
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avg = sum(e.length for e in entries) / max(n, 1)
    tag_df = Counter(t for e in entries for t in e.tag_terms)
    return entries, idf, avg, tag_df


def search(question: str, k: int = 3) -> list[Hit]:
    entries, idf, avg, tag_df = _index()
    q = set(tokens(question))
    k1, b = 1.4, 0.75
    hits = []
    for e in entries:
        score = 0.0
        for t in q:
            f = e.tf.get(t, 0)
            if f:
                score += idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * e.length / avg))
        if score:
            tag_hits = sorted(q & e.tag_terms)
            matched = sum(1 for t in q if t in e.tf)
            hits.append(Hit(e.doc, e.section, score, tag_hits,
                            [t for t in tag_hits if tag_df[t] == 1], matched / len(q)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]


def supports(text: str, section_number: str) -> bool:
    """True if `text` on its own confidently retrieves that section. Used to check an LLM-rewritten query
    against the student's original words, so added keywords cannot manufacture an answer."""
    return any(h.section.number == section_number and is_confident(h) for h in search(text, k=5))


def is_confident(hit: Hit | None) -> bool:
    if not hit:
        return False
    if len(hit.tag_hits) >= MIN_TAG_HITS and hit.score >= MIN_SCORE:
        return True
    return bool(hit.unique_tag_hits) and hit.coverage >= MIN_COVERAGE_SINGLE_TAG
