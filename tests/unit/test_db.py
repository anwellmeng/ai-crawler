import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db


def _temp_db():
    """Return a (TemporaryDirectory, Path) pair for an isolated test DB."""
    tmp = tempfile.TemporaryDirectory()
    return tmp, Path(tmp.name) / "test.db"


class TestInitDb(unittest.TestCase):
    def test_init_db_creates_schema(self):
        tmp, db_path = _temp_db()
        with tmp, patch.object(db, "DB_PATH", db_path):
            db.init_db()
            with db.get_conn() as conn:
                cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(authors)").fetchall()
                }
        expected = {
            "url", "crawl_status", "markdown", "crawl_error",
            "analyze_status", "analyze_error", "emails", "contact_links",
            "created_at", "updated_at",
        }
        self.assertTrue(expected.issubset(cols))

    def test_init_db_idempotent(self):
        tmp, db_path = _temp_db()
        with tmp, patch.object(db, "DB_PATH", db_path):
            db.init_db()
            db.init_db()  # second call must not raise


class TestGetConn(unittest.TestCase):
    def test_commits_on_success(self):
        tmp, db_path = _temp_db()
        with tmp, patch.object(db, "DB_PATH", db_path):
            db.init_db()
            with db.get_conn() as conn:
                conn.execute("INSERT INTO authors (url) VALUES (?)", ("https://example.com/",))
            with db.get_conn() as conn:
                row = conn.execute("SELECT url FROM authors").fetchone()
        self.assertEqual(row["url"], "https://example.com/")

    def test_rollback_on_exception(self):
        tmp, db_path = _temp_db()
        with tmp, patch.object(db, "DB_PATH", db_path):
            db.init_db()
            try:
                with db.get_conn() as conn:
                    conn.execute("INSERT INTO authors (url) VALUES (?)", ("https://example.com/",))
                    raise RuntimeError("simulated failure")
            except RuntimeError:
                pass
            with db.get_conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        self.assertEqual(count, 0)

    def test_row_factory_named_access(self):
        tmp, db_path = _temp_db()
        with tmp, patch.object(db, "DB_PATH", db_path):
            db.init_db()
            with db.get_conn() as conn:
                conn.execute("INSERT INTO authors (url) VALUES (?)", ("https://example.com/",))
            with db.get_conn() as conn:
                row = conn.execute("SELECT url, crawl_status FROM authors").fetchone()
        self.assertEqual(row["url"], "https://example.com/")
        self.assertEqual(row["crawl_status"], "pending")


if __name__ == "__main__":
    unittest.main()
