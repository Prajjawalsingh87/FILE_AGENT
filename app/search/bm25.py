"""A small, dependency-free BM25 full-text index.

Documents are tokenized into lowercase word tokens, stored in an inverted index
(term -> {doc_id: term frequency}) and ranked with Okapi BM25. Snippets are cut
around the densest cluster of query terms and the matched words are wrapped in
``<mark>`` tags after HTML-escaping the surrounding text, so they are safe to
render in a browser.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

MAX_QUERY_LENGTH = 256
MAX_QUERY_TERMS = 32
MAX_LIMIT = 50
MAX_OFFSET = 1000
MAX_TOKEN_LENGTH = 64
SNIPPET_CHARS = 200

STOPWORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or such that the "
    "their then there these they this to was will with".split()
)


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Lowercase word tokens; underscores and punctuation split words."""
    tokens = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group()
        if len(token) > MAX_TOKEN_LENGTH:
            continue
        if not keep_stopwords and token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def query_terms(query: str) -> list[str]:
    """Unique query terms in order, bounded by MAX_QUERY_TERMS."""
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Query must be at most {MAX_QUERY_LENGTH} characters.")
    terms = tokenize(query) or tokenize(query, keep_stopwords=True)
    return list(dict.fromkeys(terms))[:MAX_QUERY_TERMS]


@dataclass(frozen=True)
class SearchHit:
    path: str
    score: float
    snippet: str
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": round(self.score, 4),
            "snippet": self.snippet,
            "matched_terms": self.matched_terms,
        }


@dataclass
class _Doc:
    path: str
    text: str
    length: int


class BM25Index:
    """In-memory inverted index with Okapi BM25 scoring."""

    def __init__(self, k1: float = 1.2, b: float = 0.75, title_boost: int = 2) -> None:
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        self._docs: list[_Doc] = []
        self._postings: dict[str, dict[int, int]] = {}
        self._total_length = 0

    def __len__(self) -> int:
        return len(self._docs)

    @property
    def avg_length(self) -> float:
        return self._total_length / len(self._docs) if self._docs else 0.0

    def add(self, path: str, text: str) -> None:
        doc_id = len(self._docs)
        # File-name words count as extra occurrences so "notes" finds notes.txt.
        tokens = tokenize(text) + tokenize(path) * self.title_boost
        self._docs.append(_Doc(path=path, text=text, length=len(tokens)))
        self._total_length += len(tokens)
        for term, tf in Counter(tokens).items():
            self._postings.setdefault(term, {})[doc_id] = tf

    def idf(self, term: str) -> float:
        n = len(self._postings.get(term, ()))
        # BM25+ style floor (the +1 inside log) keeps IDF positive for common terms.
        return math.log(1 + (len(self._docs) - n + 0.5) / (n + 0.5))

    def search(self, query: str, limit: int = 10, offset: int = 0) -> tuple[int, list[SearchHit]]:
        """Return (total matching docs, page of hits ordered by score)."""
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}.")
        if not 0 <= offset <= MAX_OFFSET:
            raise ValueError(f"offset must be between 0 and {MAX_OFFSET}.")

        terms = query_terms(query)
        if not terms or not self._docs:
            return 0, []

        avg = self.avg_length or 1.0
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self.idf(term)
            for doc_id, tf in postings.items():
                norm = self.k1 * (1 - self.b + self.b * self._docs[doc_id].length / avg)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1) / (tf + norm)
                matched.setdefault(doc_id, []).append(term)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], self._docs[item[0]].path))
        page = ranked[offset : offset + limit]
        hits = [
            SearchHit(
                path=self._docs[doc_id].path,
                score=score,
                snippet=make_snippet(self._docs[doc_id].text, matched[doc_id]),
                matched_terms=matched[doc_id],
            )
            for doc_id, score in page
        ]
        return len(ranked), hits


def make_snippet(text: str, terms: list[str], width: int = SNIPPET_CHARS) -> str:
    """HTML-safe excerpt around the best cluster of terms, with <mark> highlights."""
    wanted = set(terms)
    positions = [m for m in TOKEN_RE.finditer(text) if m.group().lower() in wanted]

    if not positions:
        start = 0
    else:
        # Pick the window start that covers the most distinct matched terms.
        best_start, best_cover = positions[0].start(), -1
        for i, anchor in enumerate(positions):
            cover = {
                m.group().lower()
                for m in positions[i:]
                if m.end() - anchor.start() <= width
            }
            if len(cover) > best_cover:
                best_start, best_cover = anchor.start(), len(cover)
        start = max(0, best_start - width // 4)

    end = min(len(text), start + width)
    # Snap to word boundaries so we never cut a word in half.
    if start > 0:
        space = text.rfind(" ", 0, start)
        start = space + 1 if space != -1 and start - space < 20 else start
    if end < len(text):
        space = text.find(" ", end)
        end = space if space != -1 and space - end < 20 else end

    excerpt = text[start:end]
    out: list[str] = []
    cursor = 0
    for m in TOKEN_RE.finditer(excerpt):
        if m.group().lower() in wanted:
            out.append(html.escape(excerpt[cursor : m.start()]))
            out.append(f"<mark>{html.escape(m.group())}</mark>")
            cursor = m.end()
    out.append(html.escape(excerpt[cursor:]))
    snippet = " ".join("".join(out).split())
    return f"{'… ' if start > 0 else ''}{snippet}{' …' if end < len(text) else ''}"
