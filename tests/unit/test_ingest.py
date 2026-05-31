import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db
import ingest

AUTHOR_URLS = [
    "https://example.com",
    "https://example.org",
]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


class TestIngest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.csv_path = self.tmp / "authors.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", self.csv_path):
            return ingest.ingest()

    def _rows(self):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                return conn.execute("SELECT * FROM authors").fetchall()

    def test_ingest_five_urls(self):
        _write_csv(self.csv_path, [[u] for u in AUTHOR_URLS])
        result = self._run()
        self.assertEqual(result, 0)
        rows = self._rows()
        self.assertEqual(len(rows), len(AUTHOR_URLS))
        for row in rows:
            self.assertEqual(row["crawl_status"], "pending")

    def test_ingest_skips_header_row(self):
        _write_csv(self.csv_path, [["url"]] + [[u] for u in AUTHOR_URLS])
        self._run()
        rows = self._rows()
        self.assertEqual(len(rows), len(AUTHOR_URLS))
        urls = {r["url"] for r in rows}
        self.assertNotIn("url", urls)

    def test_ingest_idempotent(self):
        _write_csv(self.csv_path, [[u] for u in AUTHOR_URLS])
        self._run()
        self._run()
        rows = self._rows()
        self.assertEqual(len(rows), len(AUTHOR_URLS))

    def test_ingest_preserves_crawl_status(self):
        _write_csv(self.csv_path, [[AUTHOR_URLS[0]]])
        self._run()
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE authors SET crawl_status = 'crawled' WHERE url = ?",
                    (AUTHOR_URLS[0],),
                )
        self._run()
        rows = self._rows()
        self.assertEqual(rows[0]["crawl_status"], "crawled")

    def test_ingest_missing_csv(self):
        result = self._run()
        self.assertEqual(result, 1)

    def test_ingest_skips_blocked_domains(self):
        blocked = [
            "https://www.amazon.com/dp/B001234",
            "https://amzn.to/abc123",
            "https://a.co/xyz",
            "https://facebook.com/authorname",
            "https://m.instagram.com/author",
            "https://twitter.com/author",
            "https://x.com/author",
            "https://linkedin.com/in/author",
            "https://youtube.com/channel/abc",
            "https://tiktok.com/@author",
            "https://pinterest.com/author",
            "https://goodreads.com/author/show/123",
        ]
        _write_csv(self.csv_path, [[u] for u in blocked] + [["https://example.com"]])
        self._run()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://example.com")

    def test_ingest_blocked_domains_not_counted_as_skipped(self, capsys=None):
        _write_csv(self.csv_path, [
            ["https://amazon.com/dp/B001"],
            ["https://example.com"],
        ])
        result = self._run()
        self.assertEqual(result, 0)
        rows = self._rows()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
