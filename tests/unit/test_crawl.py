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


class FakeCrawler:
    def __init__(self):
        self.called_urls: list[str] = []

    async def arun(self, url, config=None):
        self.called_urls.append(url)
        return [SimpleNamespace(success=True, markdown=FAKE_MARKDOWN)]


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
        fake = FakeCrawler()
        self._run_crawl(fake)
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


if __name__ == "__main__":
    unittest.main()
