import asyncio
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
import analyze

TEST_URL = "https://example.com"
GOOD_RESPONSE = json.dumps({"emails": ["author@example.com"], "contact_links": ["https://example.com/contact"]})
SHORT_TEXT = "hi"
# Realistic-length markdown for tests that should reach the LLM (> _MIN_USEFUL_TOKENS=20)
CONTACT_MARKDOWN = (
    "Welcome to my author website. I write literary fiction and non-fiction. "
    "For speaking engagements and media inquiries, email me at author@example.com. "
    "You can also fill in the contact form at https://example.com/contact."
) * 3
# Large enough to exceed LLM_TOKEN_LIMIT (122_000 tokens); "word " ≈ 1 token each
LONG_TEXT = "word " * 130_000


def _make_fake_openai(content: str = GOOD_RESPONSE, raises=None):
    """Return a mock AsyncOpenAI class whose completions return `content`."""
    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    if raises:
        mock_client.chat.completions.create = AsyncMock(side_effect=raises)
    else:
        mock_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
        )
    return mock_cls


class TestTokenHelpers(unittest.TestCase):
    def test_token_count_returns_positive_int(self):
        result = analyze._token_count("hello world")
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_truncate_shortens_long_text(self):
        result = analyze._truncate(LONG_TEXT, 10)
        self.assertLessEqual(analyze._token_count(result), 10)

    def test_truncate_noop_when_short(self):
        short = "hello world"
        result = analyze._truncate(short, 1000)
        self.assertEqual(result, short)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        with patch.object(db, "DB_PATH", self.db_path):
            db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_url(self, url: str, markdown: str = "", analyze_status: str = "pending",
                    crawl_status: str = "crawled") -> None:
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO authors (url, crawl_status, markdown, analyze_status)
                       VALUES (?, ?, ?, ?)""",
                    (url, crawl_status, markdown, analyze_status),
                )

    def _get_row(self, url: str):
        with patch.object(db, "DB_PATH", self.db_path):
            with db.get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM authors WHERE url = ?", (url,)
                ).fetchone()

    def _run_analyze(self, fake_openai_cls):
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(analyze, "AsyncOpenAI", fake_openai_cls), \
             patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            return asyncio.run(analyze.analyze())

    # ── skipped ───────────────────────────────────────────────────────────────

    def test_marks_skipped_for_empty_markdown(self):
        self._insert_url(TEST_URL, markdown="")
        self._run_analyze(_make_fake_openai())
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "skipped")

    def test_marks_skipped_for_short_markdown(self):
        self._insert_url(TEST_URL, markdown=SHORT_TEXT)
        self._run_analyze(_make_fake_openai())
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "skipped")

    # ── done ──────────────────────────────────────────────────────────────────

    def test_marks_done_on_valid_response(self):
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai())
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "done")
        self.assertIn("author@example.com", row["emails"])
        self.assertIn("https://example.com/contact", row["contact_links"])

    def test_emails_stored_semicolon_separated(self):
        multi = json.dumps({"emails": ["a@x.com", "b@x.com"], "contact_links": []})
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(content=multi))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["emails"], "a@x.com; b@x.com")

    # ── failed ────────────────────────────────────────────────────────────────

    def test_marks_failed_on_bad_json(self):
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(content="not json at all"))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "failed")
        self.assertIsNotNone(row["analyze_error"])

    def test_marks_failed_on_api_error(self):
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(raises=RuntimeError("api down")))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "failed")

    # ── truncation ────────────────────────────────────────────────────────────

    def test_truncates_before_sending(self):
        original_tokens = analyze._token_count(LONG_TEXT)
        self.assertGreater(original_tokens, analyze.LLM_TOKEN_LIMIT)  # guard: must actually be over limit

        self._insert_url(TEST_URL, markdown=LONG_TEXT)
        fake_cls = _make_fake_openai()
        self._run_analyze(fake_cls)
        call_args = fake_cls.return_value.chat.completions.create.call_args
        sent_content = call_args.kwargs["messages"][1]["content"]
        self.assertLessEqual(analyze._token_count(sent_content), analyze.LLM_TOKEN_LIMIT)

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_skips_already_done_rows(self):
        self._insert_url(TEST_URL, markdown="Contact me at test@example.com.",
                         analyze_status="done")
        fake_cls = _make_fake_openai()
        self._run_analyze(fake_cls)
        fake_cls.return_value.chat.completions.create.assert_not_called()

    # ── robust parsing ─────────────────────────────────────────────────────────

    def test_parses_fenced_json(self):
        fenced = "```json\n" + GOOD_RESPONSE + "\n```"
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(content=fenced))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "done")
        self.assertIn("author@example.com", row["emails"])

    def test_marks_failed_on_none_content(self):
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(content=None))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "failed")
        self.assertIn("empty model response", row["analyze_error"])

    def test_coerces_string_emails(self):
        # Model returns emails as a bare string instead of a list.
        weird = json.dumps({"emails": "solo@example.com", "contact_links": []})
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        self._run_analyze(_make_fake_openai(content=weird))
        row = self._get_row(TEST_URL)
        self.assertEqual(row["emails"], "solo@example.com")

    def test_retries_transient_error_then_succeeds(self):
        good = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD_RESPONSE))]
        )
        mock_cls = MagicMock()
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[RuntimeError("rate limited"), good]
        )
        self._insert_url(TEST_URL, markdown=CONTACT_MARKDOWN)
        # Treat RuntimeError as transient and skip the real backoff sleep.
        with patch.object(analyze, "_RETRYABLE", (RuntimeError,)), \
             patch.object(analyze.asyncio, "sleep", new=AsyncMock()):
            self._run_analyze(mock_cls)
        row = self._get_row(TEST_URL)
        self.assertEqual(row["analyze_status"], "done")
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


class TestParseResponse(unittest.TestCase):
    def test_plain_json(self):
        emails, links = analyze._parse_response(GOOD_RESPONSE)
        self.assertEqual(emails, ["author@example.com"])
        self.assertEqual(links, ["https://example.com/contact"])

    def test_fenced_json(self):
        emails, _ = analyze._parse_response("```json\n" + GOOD_RESPONSE + "\n```")
        self.assertEqual(emails, ["author@example.com"])

    def test_none_raises_value_error(self):
        with self.assertRaises(ValueError):
            analyze._parse_response(None)

    def test_garbage_raises_json_error(self):
        with self.assertRaises(json.JSONDecodeError):
            analyze._parse_response("not json at all")

    def test_coerces_and_strips(self):
        emails, links = analyze._parse_response(
            json.dumps({"emails": "  a@x.com ", "contact_links": ["", "u", 5]})
        )
        self.assertEqual(emails, ["a@x.com"])
        self.assertEqual(links, ["u"])


if __name__ == "__main__":
    unittest.main()
