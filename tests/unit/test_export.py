import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db
import export


class TestExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.csv_path = self.tmp / "export.csv"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_row(self, url, analyze_status="done", emails="", contact_links="",
                    crawl_status="crawled", markdown=None):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO authors
                           (url, crawl_status, analyze_status, emails, contact_links, markdown)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (url, crawl_status, analyze_status, emails, contact_links, markdown),
                )

    def _run_export(self):
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(export, "AUTHORS_CONTACTS_CSV", self.csv_path):
            return export.export()

    def test_writes_header_row(self):
        self._insert_row("https://example.com/", emails="a@b.com")
        self._run_export()
        with self.csv_path.open() as f:
            header = next(csv.reader(f))
        self.assertEqual(header, ["url", "emails", "contact_links"])

    def test_writes_correct_data(self):
        self._insert_row("https://example.com/", emails="a@b.com",
                         contact_links="https://example.com/contact")
        self._run_export()
        with self.csv_path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://example.com/")
        self.assertEqual(rows[0]["emails"], "a@b.com")
        self.assertEqual(rows[0]["contact_links"], "https://example.com/contact")

    def test_only_done_rows_exported(self):
        self._insert_row("https://done.com/", analyze_status="done", emails="d@b.com")
        self._insert_row("https://failed.com/", analyze_status="failed")
        self._insert_row("https://pending.com/", analyze_status="pending")
        self._insert_row("https://skipped.com/", analyze_status="skipped")
        self._run_export()
        with self.csv_path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://done.com/")

    def test_fresh_write_does_not_append(self):
        self._insert_row("https://a.com/", emails="a@b.com")
        self._run_export()
        self._run_export()  # second run on identical data
        with self.csv_path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)

    def test_no_done_rows_returns_zero_and_no_file(self):
        self._insert_row("https://failed.com/", analyze_status="failed")
        result = self._run_export()
        self.assertEqual(result, 0)
        self.assertFalse(self.csv_path.exists())

    def test_null_fields_become_empty_string(self):
        self._insert_row("https://example.com/", emails=None, contact_links=None)
        self._run_export()
        with self.csv_path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["emails"], "")
        self.assertEqual(rows[0]["contact_links"], "")

    def test_multiple_rows_all_exported(self):
        for i in range(3):
            self._insert_row(f"https://author{i}.com/", emails=f"a{i}@b.com")
        self._run_export()
        with self.csv_path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)


class TestDumpMarkdown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.outputs_dir = self.tmp / "outputs"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_row(self, url, markdown=None):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO authors (url, markdown) VALUES (?, ?)",
                    (url, markdown),
                )

    def _run_dump(self, url=None):
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(export, "OUTPUTS_DIR", self.outputs_dir):
            return export.dump_markdown(url)

    def test_dump_all_creates_file_per_author(self):
        self._insert_row("https://a.com/", markdown="# A")
        self._insert_row("https://b.com/", markdown="# B")
        self._run_dump()
        files = list((self.outputs_dir / "md_dumps").iterdir())
        self.assertEqual(len(files), 2)

    def test_dump_single_url_creates_one_file(self):
        self._insert_row("https://a.com/", markdown="# A")
        self._insert_row("https://b.com/", markdown="# B")
        self._run_dump("https://a.com/")
        files = list((self.outputs_dir / "md_dumps").iterdir())
        self.assertEqual(len(files), 1)

    def test_file_content_matches_markdown(self):
        self._insert_row("https://a.com/", markdown="# Hello World")
        self._run_dump("https://a.com/")
        files = list((self.outputs_dir / "md_dumps").iterdir())
        self.assertEqual(files[0].read_text(encoding="utf-8"), "# Hello World")

    def test_filename_is_deterministic(self):
        url = "https://a.com/"
        self._insert_row(url, markdown="content")
        self._run_dump(url)
        slug = hashlib.md5(url.encode()).hexdigest()[:12]
        expected = self.outputs_dir / "md_dumps" / f"{slug}.md"
        self.assertTrue(expected.exists())

    def test_no_matching_url_returns_one(self):
        result = self._run_dump("https://notexist.com/")
        self.assertEqual(result, 1)

    def test_null_markdown_rows_are_skipped(self):
        self._insert_row("https://a.com/", markdown=None)
        result = self._run_dump()
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
