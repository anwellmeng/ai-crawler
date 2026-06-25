import argparse
import asyncio
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db
import ingest
import crawl
import analyze
import export
import pipeline
from tests.integration import AUTHOR_URLS, make_authors_csv

FAKE_MARKDOWN = (
    "Welcome to my author website. For speaking engagements and media inquiries, "
    "email me at author@example.com or use the contact form at https://example.com/contact. "
    "I write award-winning fiction and non-fiction for readers of all ages. "
) * 3
FAKE_LLM_RESPONSE = json.dumps({
    "emails": ["author@example.com"],
    "contact_links": ["https://example.com/contact"],
})


class FakeCrawler:
    async def arun(self, url, config=None):
        return SimpleNamespace(
            success=True,
            markdown=FAKE_MARKDOWN,
            links={"internal": [], "external": []},
        )


def _make_crawler_ctx():
    class FakeCtx:
        def __init__(self, config=None):
            pass
        async def __aenter__(self):
            return FakeCrawler()
        async def __aexit__(self, *_):
            return False
    return FakeCtx


def _make_fake_openai():
    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=FAKE_LLM_RESPONSE))]
        )
    )
    return mock_cls


class TestFullPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.csv_path = self.tmp / "authors.csv"
        make_authors_csv(self.csv_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _all_rows(self):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                return conn.execute("SELECT * FROM authors").fetchall()

    def test_full_pipeline(self):
        # ── Stage 1: ingest ───────────────────────────────────────────────────
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", self.csv_path):
            result = ingest.ingest()
        self.assertEqual(result, 0)

        rows = self._all_rows()
        self.assertEqual(len(rows), len(AUTHOR_URLS))
        self.assertEqual({r["url"] for r in rows}, set(AUTHOR_URLS))
        for row in rows:
            self.assertEqual(row["crawl_status"], "pending")

        # ── Stage 2: crawl ────────────────────────────────────────────────────
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(crawl, "AsyncWebCrawler", _make_crawler_ctx()):
            result = asyncio.run(crawl.crawl())
        self.assertEqual(result, 0)

        rows = self._all_rows()
        for row in rows:
            self.assertEqual(row["crawl_status"], "crawled")
            self.assertIsNotNone(row["markdown"])

        # ── Stage 3: analyze ──────────────────────────────────────────────────
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(analyze, "AsyncOpenAI", _make_fake_openai()), \
             patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = asyncio.run(analyze.analyze())
        self.assertEqual(result, 0)

        rows = self._all_rows()
        for row in rows:
            self.assertEqual(row["analyze_status"], "done")
            self.assertIn("author@example.com", row["emails"])
            self.assertIn("https://example.com/contact", row["contact_links"])

        # ── Stage 4: export ───────────────────────────────────────────────────
        out_csv = self.tmp / "export.csv"
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(export, "AUTHORS_CONTACTS_CSV", out_csv):
            result = export.export()
        self.assertEqual(result, 0)

        self.assertTrue(out_csv.exists())
        with out_csv.open() as f:
            exported = list(csv.DictReader(f))
        self.assertEqual(len(exported), len(AUTHOR_URLS))
        exported_urls = {r["url"] for r in exported}
        self.assertEqual(exported_urls, set(AUTHOR_URLS))
        for row in exported:
            self.assertEqual(row["emails"], "author@example.com")
            self.assertEqual(row["contact_links"], "https://example.com/contact")

    def test_pipeline_is_resumable(self):
        """Re-running each stage leaves already-processed rows untouched."""
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", self.csv_path):
            ingest.ingest()
            ingest.ingest()  # second run must not duplicate rows

        rows = self._all_rows()
        self.assertEqual(len(rows), len(AUTHOR_URLS))

        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(crawl, "AsyncWebCrawler", _make_crawler_ctx()):
            asyncio.run(crawl.crawl())
            asyncio.run(crawl.crawl())  # second run crawls nothing

        rows = self._all_rows()
        for row in rows:
            self.assertEqual(row["crawl_status"], "crawled")

    def test_cmd_run_end_to_end(self):
        """pipeline.cmd_run drives all four stages through one call."""
        out_csv = self.tmp / "export.csv"

        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", self.csv_path), \
             patch.object(crawl, "AsyncWebCrawler", _make_crawler_ctx()), \
             patch.object(analyze, "AsyncOpenAI", _make_fake_openai()), \
             patch.object(export, "AUTHORS_CONTACTS_CSV", out_csv), \
             patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = pipeline.cmd_run(argparse.Namespace(input=None, all=False))

        self.assertEqual(result, 0)
        self.assertTrue(out_csv.exists())

        with out_csv.open() as f:
            exported = list(csv.DictReader(f))
        self.assertEqual(len(exported), len(AUTHOR_URLS))
        for row in exported:
            self.assertIn(row["url"], AUTHOR_URLS)
            self.assertEqual(row["emails"], "author@example.com")
            self.assertEqual(row["contact_links"], "https://example.com/contact")

    def test_cmd_run_aborts_on_stage_failure(self):
        """cmd_run stops and returns non-zero if any stage fails."""
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", self.tmp / "nonexistent.csv"):
            result = pipeline.cmd_run(argparse.Namespace(input=None, all=False))

        self.assertNotEqual(result, 0)
        # crawl never ran — all rows still pending (table is empty in this case)
        rows = self._all_rows()
        self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
