"""Documentation content, organized one module per category.

Each module exposes a ``CATEGORY`` (:class:`DocCategory`) and an ``ARTICLES``
list (:class:`DocArticle`). Add a new category by creating a module here and
registering it in ``_MODULES``.
"""

from __future__ import annotations

from ..model import DocArticle, DocCategory
from . import (
    adapters,
    ai_culling,
    export,
    getting_started,
    library,
    reference,
    reviewing,
    settings,
)

_MODULES = [
    getting_started,
    reviewing,
    ai_culling,
    adapters,
    library,
    export,
    settings,
    reference,
]


def all_categories() -> list[DocCategory]:
    return [module.CATEGORY for module in _MODULES]


def all_articles() -> list[DocArticle]:
    articles: list[DocArticle] = []
    for module in _MODULES:
        articles.extend(module.ARTICLES)
    return articles
