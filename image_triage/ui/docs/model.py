"""Content model for the in-app documentation wiki.

Articles are plain Markdown with a stable ``id`` so other articles (and the
rest of the app) can deep-link to them with ``doc:<id>`` or
``doc:<id>#heading-anchor`` links. Categories group articles in the navigation
tree. Keep this module dependency-free so content and tests can import it
without pulling in any Qt widgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DocCategory:
    """A top-level grouping in the documentation navigation tree."""

    id: str
    title: str
    order: int = 0
    icon: str = ""
    summary: str = ""


@dataclass(frozen=True, slots=True)
class DocArticle:
    """A single documentation page, authored in Markdown.

    ``summary`` is shown in search results and category landing pages, so keep
    it to one plain sentence. ``keywords`` boost search ranking for terms that
    may not appear verbatim in the title or body.
    """

    id: str
    title: str
    category: str
    markdown: str
    summary: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
