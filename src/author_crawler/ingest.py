"""
Ingest stage.

Reads the first column of authors.csv and inserts each URL into the
database.  INSERT OR IGNORE makes every run safe to re-run: existing
rows are left untouched so their crawl/analyze progress is preserved.
"""

from __future__ import annotations

import csv
import itertools
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from config import AUTHORS_CSV
from db import get_conn, init_db
from utils import is_blocked_url

logger = logging.getLogger(__name__)


def _looks_like_header(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return False

    if normalized in {"url", "author_url", "author website", "author_website"}:
        return True

    parsed = urlparse(value.strip())
    return not (parsed.scheme and parsed.netloc)


def ingest(csv_path: Path | None = None) -> int:
    init_db()

    path = Path(csv_path) if csv_path else AUTHORS_CSV

    if not path.exists():
        logger.error("Authors CSV not found: %s", path)
        print(f"Error: {path} not found.")
        return 1

    inserted = 0
    skipped = 0
    blocked = 0

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        with get_conn() as conn:
            # One batch per ingest run.  Every URL in this file is stamped with it
            # (new and pre-existing alike) so export can scope to the latest run.
            batch_id = conn.execute(
                "SELECT COALESCE(MAX(batch_id), 0) + 1 FROM authors"
            ).fetchone()[0]

            if first_row and first_row[0].strip() and not _looks_like_header(first_row[0]):
                reader = itertools.chain([first_row], reader)

            for row in reader:
                if not row or not row[0].strip():
                    continue

                url = row[0].strip()

                if is_blocked_url(url):
                    blocked += 1
                    continue

                exists = conn.execute(
                    "SELECT 1 FROM authors WHERE url = ?", (url,)
                ).fetchone()

                # Stamp the batch on insert and on re-ingest; crawl/analyze
                # progress is left untouched so reruns stay safe.
                conn.execute(
                    """INSERT INTO authors (url, batch_id) VALUES (?, ?)
                       ON CONFLICT(url) DO UPDATE
                       SET batch_id   = excluded.batch_id,
                           updated_at = datetime('now')""",
                    (url, batch_id),
                )

                if exists:
                    skipped += 1
                else:
                    inserted += 1

    logger.info("Ingest complete (%s, batch %d): %d new, %d already present, %d blocked", path, batch_id, inserted, skipped, blocked)
    blocked_msg = f" {blocked} blocked domain(s) skipped." if blocked else ""
    print(f"Ingested {inserted} new author(s). ({skipped} already in database.{blocked_msg})")
    return 0


if __name__ == "__main__":
    raise SystemExit(ingest())
