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

    def test_first_ingest_stamps_batch_one(self):
        _write_csv(self.csv_path, [[u] for u in AUTHOR_URLS])
        self._run()
        rows = self._rows()
        self.assertTrue(all(r["batch_id"] == 1 for r in rows))

    def test_second_ingest_of_new_file_increments_batch(self):
        _write_csv(self.csv_path, [[AUTHOR_URLS[0]]])
        self._run()  # batch 1
        _write_csv(self.csv_path, [[AUTHOR_URLS[1]]])
        self._run()  # batch 2
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                b1 = conn.execute(
                    "SELECT batch_id FROM authors WHERE url = ?", (AUTHOR_URLS[0],)
                ).fetchone()[0]
                b2 = conn.execute(
                    "SELECT batch_id FROM authors WHERE url = ?", (AUTHOR_URLS[1],)
                ).fetchone()[0]
        self.assertEqual(b1, 1)
        self.assertEqual(b2, 2)

    def test_reingest_restamps_existing_url_to_latest_batch(self):
        # An existing URL re-fed in a later run moves to the newest batch,
        # but keeps its crawl progress.
        _write_csv(self.csv_path, [[AUTHOR_URLS[0]]])
        self._run()  # batch 1
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE authors SET crawl_status = 'crawled' WHERE url = ?",
                    (AUTHOR_URLS[0],),
                )
        _write_csv(self.csv_path, [[AUTHOR_URLS[1]], [AUTHOR_URLS[0]]])
        self._run()  # batch 2 — claims both
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT batch_id, crawl_status FROM authors WHERE url = ?",
                    (AUTHOR_URLS[0],),
                ).fetchone()
        self.assertEqual(row["batch_id"], 2)
        self.assertEqual(row["crawl_status"], "crawled")

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


class TestIngestCustomPath(unittest.TestCase):
    """Tests for the csv_path argument added to ingest()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, csv_path):
        with patch.object(db, "DB_PATH", self.db_path):
            return ingest.ingest(csv_path)

    def _rows(self):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                return conn.execute("SELECT url FROM authors").fetchall()

    def test_custom_path_ingests_urls(self):
        custom = self.tmp / "my_authors.csv"
        _write_csv(custom, [["https://example.com"], ["https://example.org"]])
        result = self._run(custom)
        self.assertEqual(result, 0)
        urls = {r["url"] for r in self._rows()}
        self.assertEqual(urls, {"https://example.com", "https://example.org"})

    def test_custom_path_missing_returns_error(self):
        missing = self.tmp / "nonexistent.csv"
        result = self._run(missing)
        self.assertEqual(result, 1)

    def test_custom_path_string_accepted(self):
        custom = self.tmp / "str_path.csv"
        _write_csv(custom, [["https://example.com"]])
        result = self._run(str(custom))
        self.assertEqual(result, 0)
        self.assertEqual(len(self._rows()), 1)

    def test_custom_path_does_not_require_default_csv(self):
        """Passing a custom path should not fall back to AUTHORS_CSV."""
        custom = self.tmp / "custom.csv"
        _write_csv(custom, [["https://example.com"]])
        fake_default = self.tmp / "authors.csv"
        # fake_default intentionally not created
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", fake_default):
            result = ingest.ingest(custom)
        self.assertEqual(result, 0)

    def test_none_falls_back_to_default(self):
        default = self.tmp / "authors.csv"
        _write_csv(default, [["https://example.com"]])
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(ingest, "AUTHORS_CSV", default):
            result = ingest.ingest(None)
        self.assertEqual(result, 0)
        self.assertEqual(len(self._rows()), 1)


if __name__ == "__main__":
    unittest.main()
