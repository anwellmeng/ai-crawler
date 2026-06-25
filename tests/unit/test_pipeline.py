import argparse
import csv
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "author_crawler"))

import db
import export
import ingest as ingest_mod
import pipeline


class TestBuildParser(unittest.TestCase):
    def setUp(self):
        self.parser = pipeline._build_parser()

    def test_all_commands_registered(self):
        for cmd in ("ingest", "crawl", "analyze", "export", "run", "status", "dump-md"):
            args = self.parser.parse_args([cmd])
            self.assertEqual(args.command, cmd)

    def test_dump_md_url_is_optional(self):
        args = self.parser.parse_args(["dump-md"])
        self.assertIsNone(args.url)

    def test_dump_md_url_is_captured(self):
        args = self.parser.parse_args(["dump-md", "https://example.com/"])
        self.assertEqual(args.url, "https://example.com/")

    def test_no_command_exits(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args([])

    # --input / -i on ingest
    def test_ingest_input_long_flag(self):
        args = self.parser.parse_args(["ingest", "--input", "some/file.csv"])
        self.assertEqual(args.input, "some/file.csv")

    def test_ingest_input_short_flag(self):
        args = self.parser.parse_args(["ingest", "-i", "some/file.csv"])
        self.assertEqual(args.input, "some/file.csv")

    def test_ingest_input_default_is_none(self):
        args = self.parser.parse_args(["ingest"])
        self.assertIsNone(args.input)

    # --input / -i on run
    def test_run_input_long_flag(self):
        args = self.parser.parse_args(["run", "--input", "data/inputs/custom.csv"])
        self.assertEqual(args.input, "data/inputs/custom.csv")

    def test_run_input_short_flag(self):
        args = self.parser.parse_args(["run", "-i", "data/inputs/custom.csv"])
        self.assertEqual(args.input, "data/inputs/custom.csv")

    def test_run_input_default_is_none(self):
        args = self.parser.parse_args(["run"])
        self.assertIsNone(args.input)

    # crawl/analyze/export have no --input flag
    def test_crawl_has_no_input_flag(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["crawl", "--input", "x.csv"])


class TestCmdIngestInputRouting(unittest.TestCase):
    """cmd_ingest must forward args.input to ingest()."""

    def _run(self, input_val):
        args = argparse.Namespace(input=input_val)
        mock = MagicMock(return_value=0)
        with patch.object(ingest_mod, "ingest", mock):
            pipeline.cmd_ingest(args)
        return mock

    def test_forwards_custom_path(self):
        mock = self._run("data/inputs/custom.csv")
        mock.assert_called_once_with("data/inputs/custom.csv")

    def test_forwards_none_when_no_flag(self):
        mock = self._run(None)
        mock.assert_called_once_with(None)


class TestCmdRunInputRouting(unittest.TestCase):
    """cmd_run must pass args.input to the ingest stage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.csv_path = self.tmp / "custom.csv"
        with self.csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["https://example.com"])

    def tearDown(self):
        self._tmp.cleanup()

    def _run_with_fakes(self, input_val):
        captured = []

        def fake_ingest(csv_path=None):
            captured.append(csv_path)
            return 0

        async def fake_crawl():
            return 0

        async def fake_analyze():
            return 0

        def fake_export():
            return 0

        args = argparse.Namespace(input=input_val)
        # cmd_run does local imports ("from ingest import ingest"), so patch
        # the function on the already-loaded module object.
        import crawl as crawl_mod
        import analyze as analyze_mod
        import export as export_mod
        with patch.object(ingest_mod, "ingest", fake_ingest), \
             patch.object(crawl_mod, "crawl", fake_crawl), \
             patch.object(analyze_mod, "analyze", fake_analyze), \
             patch.object(export_mod, "export", fake_export):
            pipeline.cmd_run(args)

        return captured

    def test_run_passes_custom_path_to_ingest(self):
        captured = self._run_with_fakes(str(self.csv_path))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], str(self.csv_path))

    def test_run_passes_none_when_no_flag(self):
        captured = self._run_with_fakes(None)
        self.assertEqual(captured[0], None)


class TestCmdStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_row(self, url, crawl_status="pending", analyze_status="pending"):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO authors (url, crawl_status, analyze_status) VALUES (?, ?, ?)",
                    (url, crawl_status, analyze_status),
                )

    def _run_status(self):
        with patch.object(db, "DB_PATH", self.db_path):
            return pipeline.cmd_status(argparse.Namespace())

    def _captured_status(self):
        buf = StringIO()
        with patch("builtins.print", side_effect=lambda *a: buf.write(" ".join(str(x) for x in a) + "\n")):
            self._run_status()
        return buf.getvalue()

    def test_returns_zero(self):
        self.assertEqual(self._run_status(), 0)

    def test_shows_total_author_count(self):
        self._insert_row("https://a.com/")
        self._insert_row("https://b.com/")
        self.assertIn("2", self._captured_status())

    def test_shows_crawl_statuses(self):
        self._insert_row("https://a.com/", crawl_status="pending")
        self._insert_row("https://b.com/", crawl_status="crawled", analyze_status="done")
        self._insert_row("https://c.com/", crawl_status="failed")
        text = self._captured_status()
        self.assertIn("pending", text)
        self.assertIn("crawled", text)
        self.assertIn("failed", text)

    def test_shows_analyze_status_for_crawled_rows(self):
        self._insert_row("https://b.com/", crawl_status="crawled", analyze_status="done")
        text = self._captured_status()
        self.assertIn("done", text)

    def test_empty_db_shows_none_crawled(self):
        self.assertIn("none crawled yet", self._captured_status())


class TestCmdDumpMd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"
        self.outputs_dir = self.tmp / "outputs"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_row(self, url, markdown="# test"):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO authors (url, markdown) VALUES (?, ?)",
                    (url, markdown),
                )

    def test_passes_url_arg_through(self):
        url = "https://example.com/"
        self._insert_row(url)
        args = argparse.Namespace(url=url)
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(export, "OUTPUTS_DIR", self.outputs_dir):
            result = pipeline.cmd_dump_md(args)
        self.assertEqual(result, 0)
        files = list((self.outputs_dir / "md_dumps").iterdir())
        self.assertEqual(len(files), 1)

    def test_none_url_dumps_all(self):
        self._insert_row("https://a.com/", markdown="# A")
        self._insert_row("https://b.com/", markdown="# B")
        args = argparse.Namespace(url=None)
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(export, "OUTPUTS_DIR", self.outputs_dir):
            result = pipeline.cmd_dump_md(args)
        self.assertEqual(result, 0)
        files = list((self.outputs_dir / "md_dumps").iterdir())
        self.assertEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
