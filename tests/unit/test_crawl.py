import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db
import crawl

TEST_URL = "https://example.com"
FAKE_MARKDOWN = "Contact me at test@example.com or use my contact form."


def _fake_result(markdown=FAKE_MARKDOWN, links=None):
    """Build a SimpleNamespace that matches crawl4ai's CrawlResult shape."""
    return SimpleNamespace(
        success=True,
        markdown=markdown,
        links={"internal": links or [], "external": []},
    )


class FakeCrawler:
    def __init__(self):
        self.called_urls: list[str] = []

    async def arun(self, url, config=None):
        self.called_urls.append(url)
        return _fake_result()


class FakeCrawlerWithLinks:
    """Simulates a site where the root page links to a /contact page."""

    def __init__(self):
        self.called_urls: list[str] = []

    async def arun(self, url, config=None):
        self.called_urls.append(url)
        if url == TEST_URL:
            return _fake_result(
                markdown="Home page",
                links=[
                    {"href": "https://example.com/contact", "text": "Contact"},
                    {"href": "https://example.com/blog", "text": "Blog"},
                ],
            )
        return _fake_result(markdown=f"Content of {url}")


class ErrorCrawler:
    async def arun(self, url, config=None):
        raise RuntimeError("network error")


def _make_crawler_ctx(crawler_instance):
    """Wrap a crawler in a fake async context manager."""
    class FakeCtx:
        def __init__(self, config=None):
            pass

        async def __aenter__(self):
            return crawler_instance

        async def __aexit__(self, *_):
            return False

    return FakeCtx


class TestContactLinks(unittest.TestCase):
    """Unit tests for the pure _contact_links helper."""

    def _result(self, links):
        return SimpleNamespace(links={"internal": links, "external": []})

    def test_returns_keyword_matched_links(self):
        # Only CRAWL_KEYWORDS ("contact", "email") are matched — "about" is not
        result = self._result([
            {"href": "https://example.com/contact", "text": "Contact Us"},
            {"href": "https://example.com/email-me", "text": "Drop me a line"},
            {"href": "https://example.com/about", "text": "About"},
            {"href": "https://example.com/blog", "text": "Blog"},
        ])
        links = crawl._contact_links(result, "https://example.com", limit=10)
        self.assertIn("https://example.com/contact", links)
        self.assertIn("https://example.com/email-me", links)
        self.assertNotIn("https://example.com/about", links)
        self.assertNotIn("https://example.com/blog", links)

    def test_filters_external_links(self):
        result = self._result([
            {"href": "https://other.com/contact", "text": "Contact"},
        ])
        links = crawl._contact_links(result, "https://example.com", limit=10)
        self.assertEqual(links, [])

    def test_deduplicates(self):
        result = self._result([
            {"href": "https://example.com/contact", "text": "Contact"},
            {"href": "https://example.com/contact", "text": "Contact"},
        ])
        links = crawl._contact_links(result, "https://example.com", limit=10)
        self.assertEqual(links.count("https://example.com/contact"), 1)

    def test_respects_limit(self):
        result = self._result([
            {"href": f"https://example.com/contact-{i}", "text": "Contact"}
            for i in range(10)
        ])
        links = crawl._contact_links(result, "https://example.com", limit=3)
        self.assertEqual(len(links), 3)

    def test_matches_on_link_text(self):
        result = self._result([
            {"href": "https://example.com/reach-us", "text": "Email us"},
        ])
        links = crawl._contact_links(result, "https://example.com", limit=10)
        # "email" appears in text → should be included
        self.assertIn("https://example.com/reach-us", links)

    def test_skips_base_url_itself(self):
        result = self._result([
            {"href": "https://example.com/", "text": "Contact home"},
        ])
        links = crawl._contact_links(result, "https://example.com/", limit=10)
        self.assertEqual(links, [])


class TestCrawl(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_url(self, url: str, crawl_status: str = "pending") -> None:
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO authors (url, crawl_status) VALUES (?, ?)",
                    (url, crawl_status),
                )

    def _get_row(self, url: str):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM authors WHERE url = ?", (url,)
                ).fetchone()

    def _run_crawl(self, crawler_instance):
        ctx = _make_crawler_ctx(crawler_instance)
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(crawl, "AsyncWebCrawler", ctx):
            return asyncio.run(crawl.crawl())

    def test_crawl_stores_markdown_in_db(self):
        self._insert_url(TEST_URL)
        self._run_crawl(FakeCrawler())
        row = self._get_row(TEST_URL)
        self.assertEqual(row["crawl_status"], "crawled")
        self.assertEqual(row["markdown"], FAKE_MARKDOWN)
        self.assertIsNone(row["crawl_error"])

    def test_crawl_marks_failed_on_error(self):
        self._insert_url(TEST_URL)
        self._run_crawl(ErrorCrawler())
        row = self._get_row(TEST_URL)
        self.assertEqual(row["crawl_status"], "failed")
        self.assertIsNotNone(row["crawl_error"])

    def test_crawl_skips_non_pending_rows(self):
        self._insert_url(TEST_URL, crawl_status="crawled")
        fake = FakeCrawler()
        self._run_crawl(fake)
        self.assertEqual(fake.called_urls, [])
        row = self._get_row(TEST_URL)
        self.assertEqual(row["crawl_status"], "crawled")

    def test_crawl_no_pending_returns_zero(self):
        fake = FakeCrawler()
        result = self._run_crawl(fake)
        self.assertEqual(result, 0)
        self.assertEqual(fake.called_urls, [])

    def test_crawl_follows_contact_links(self):
        """Root page contact links are crawled; non-contact links are not."""
        self._insert_url(TEST_URL)
        fake = FakeCrawlerWithLinks()
        self._run_crawl(fake)
        row = self._get_row(TEST_URL)
        self.assertEqual(row["crawl_status"], "crawled")
        self.assertIn("https://example.com/contact", fake.called_urls)
        self.assertNotIn("https://example.com/blog", fake.called_urls)
        self.assertIn("Home page", row["markdown"])
        self.assertIn("Content of https://example.com/contact", row["markdown"])


if __name__ == "__main__":
    unittest.main()
