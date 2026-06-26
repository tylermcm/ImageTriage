"""Integrity and search tests for the in-app documentation registry.

These cover the Qt-free content layer only, so they run headless.
"""

from __future__ import annotations

import unittest

from image_triage.ui.docs.content import all_articles, all_categories
from image_triage.ui.docs.registry import DocRegistry, get_registry


class DocsRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = DocRegistry(all_categories(), all_articles())

    def test_registry_builds_without_duplicate_or_orphan_ids(self) -> None:
        # DocRegistry raises on duplicate ids or unknown category refs, so a
        # clean build is the assertion. Confirm it actually has content.
        self.assertGreaterEqual(len(self.registry.all_articles()), 20)
        self.assertGreaterEqual(len(self.registry.categories()), 5)

    def test_every_category_has_at_least_one_article(self) -> None:
        for category in self.registry.categories():
            with self.subTest(category=category.id):
                self.assertTrue(
                    self.registry.articles_in(category.id),
                    f"category {category.id!r} has no articles",
                )

    def test_no_broken_cross_links(self) -> None:
        broken = self.registry.broken_links()
        self.assertEqual(broken, {}, f"unresolved doc: links found: {broken}")

    def test_home_article_resolves(self) -> None:
        home = self.registry.home_article_id()
        self.assertTrue(home)
        self.assertIsNotNone(self.registry.article(home))

    def test_search_ranks_title_matches_first(self) -> None:
        hits = self.registry.search("adapter")
        self.assertTrue(hits)
        # The dedicated adapter overview should rank at or near the top.
        top_ids = [hit.article.id for hit in hits[:3]]
        self.assertIn("what-adapters-are", top_ids)

    def test_search_requires_all_terms(self) -> None:
        hits = self.registry.search("burst zzzznotaword")
        self.assertEqual(hits, [])

    def test_search_returns_snippets(self) -> None:
        hits = self.registry.search("dispute")
        self.assertTrue(hits)
        self.assertTrue(any(hit.snippet for hit in hits))

    def test_get_registry_is_cached_singleton(self) -> None:
        self.assertIs(get_registry(), get_registry())


if __name__ == "__main__":
    unittest.main()
