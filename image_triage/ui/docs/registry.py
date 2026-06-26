"""Assembles documentation content into a queryable registry.

The registry is the single source of truth the UI talks to: it knows every
category and article, validates that links and category references are intact,
runs full-text search, and resolves ``doc:`` cross-links. It is Qt-free so it
can be unit-tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import DocArticle, DocCategory

# Matches Markdown links pointing at another article, e.g. ``[Adapters](doc:adapters-overview)``
# or ``[Scoring](doc:ai-scoring#blending)``.
_DOC_LINK_RE = re.compile(r"\]\(doc:([a-z0-9\-]+)(?:#[^)]*)?\)")
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class DocSearchHit:
    article: DocArticle
    score: float
    snippet: str


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _first_matching_snippet(markdown: str, terms: list[str], *, limit: int = 160) -> str:
    """Return a short, link-free excerpt around the first matching term."""

    for raw_line in markdown.splitlines():
        line = raw_line.strip().lstrip("#->* ").strip()
        if not line:
            continue
        lowered = line.casefold()
        if any(term in lowered for term in terms):
            clean = re.sub(r"[`*_>#\[\]]", "", line)
            clean = re.sub(r"\(doc:[^)]*\)|\(https?:[^)]*\)", "", clean).strip()
            if len(clean) > limit:
                clean = clean[: limit - 1].rstrip() + "…"
            if clean:
                return clean
    # Fall back to the first prose line.
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            clean = re.sub(r"[`*_>#\[\]]", "", line).strip()
            if len(clean) > limit:
                clean = clean[: limit - 1].rstrip() + "…"
            return clean
    return ""


class DocRegistry:
    """Indexes categories and articles and answers UI queries."""

    def __init__(self, categories: list[DocCategory], articles: list[DocArticle]) -> None:
        self._categories: dict[str, DocCategory] = {}
        for category in categories:
            if category.id in self._categories:
                raise ValueError(f"Duplicate documentation category id: {category.id!r}")
            self._categories[category.id] = category

        self._articles: dict[str, DocArticle] = {}
        for article in articles:
            if article.id in self._articles:
                raise ValueError(f"Duplicate documentation article id: {article.id!r}")
            if article.category not in self._categories:
                raise ValueError(
                    f"Article {article.id!r} references unknown category {article.category!r}"
                )
            self._articles[article.id] = article

        # Precompute a lightweight search index.
        self._index: dict[str, dict[str, int]] = {}
        for article in self._articles.values():
            counts: dict[str, int] = {}
            for token in _tokenize(article.markdown):
                counts[token] = counts.get(token, 0) + 1
            self._index[article.id] = counts

    # -- Navigation -------------------------------------------------------

    def categories(self) -> list[DocCategory]:
        return sorted(self._categories.values(), key=lambda c: (c.order, c.title.casefold()))

    def category(self, category_id: str) -> DocCategory | None:
        return self._categories.get(category_id)

    def articles_in(self, category_id: str) -> list[DocArticle]:
        return [a for a in self._articles.values() if a.category == category_id]

    def article(self, article_id: str) -> DocArticle | None:
        return self._articles.get(article_id)

    def all_articles(self) -> list[DocArticle]:
        return list(self._articles.values())

    def home_article_id(self) -> str:
        """First article of the first category, used as the landing page."""

        for category in self.categories():
            articles = self.articles_in(category.id)
            if articles:
                return articles[0].id
        return ""

    # -- Integrity --------------------------------------------------------

    def broken_links(self) -> dict[str, list[str]]:
        """Map each article id to any ``doc:`` targets that do not resolve."""

        broken: dict[str, list[str]] = {}
        for article in self._articles.values():
            missing = [
                target
                for target in _DOC_LINK_RE.findall(article.markdown)
                if target not in self._articles
            ]
            if missing:
                broken[article.id] = missing
        return broken

    # -- Search -----------------------------------------------------------

    def search(self, query: str, *, limit: int = 40) -> list[DocSearchHit]:
        terms = _tokenize(query)
        if not terms:
            return []

        hits: list[DocSearchHit] = []
        for article in self._articles.values():
            title_tokens = set(_tokenize(article.title))
            keyword_tokens = set(_tokenize(" ".join(article.keywords)))
            summary_tokens = set(_tokenize(article.summary))
            body_counts = self._index.get(article.id, {})

            score = 0.0
            matched_terms = 0
            for term in terms:
                term_score = 0.0
                if term in title_tokens:
                    term_score += 12.0
                if term in keyword_tokens:
                    term_score += 6.0
                if term in summary_tokens:
                    term_score += 3.0
                body_hits = body_counts.get(term, 0)
                if body_hits:
                    term_score += 1.0 + min(body_hits, 5) * 0.25
                if term_score > 0:
                    matched_terms += 1
                    score += term_score

            # Require every query term to appear somewhere in the article.
            if matched_terms < len(terms):
                continue
            # Exact whole-title match jumps to the top.
            if article.title.casefold() == query.casefold():
                score += 50.0

            hits.append(
                DocSearchHit(
                    article=article,
                    score=score,
                    snippet=_first_matching_snippet(article.markdown, terms),
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.article.title.casefold()))
        return hits[:limit]


_REGISTRY: DocRegistry | None = None


def get_registry() -> DocRegistry:
    """Return the process-wide documentation registry, building it once."""

    global _REGISTRY
    if _REGISTRY is None:
        from .content import all_articles, all_categories

        _REGISTRY = DocRegistry(all_categories(), all_articles())
    return _REGISTRY
