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


def ingest() -> int:
    init_db()

    if not AUTHORS_CSV.exists():
        logger.error("Authors CSV not found: %s", AUTHORS_CSV)
        print(f"Error: {AUTHORS_CSV} not found.")
        return 1

    inserted = 0
    skipped = 0
    blocked = 0

    with AUTHORS_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        with get_conn() as conn:
            if first_row and first_row[0].strip() and not _looks_like_header(first_row[0]):
                reader = itertools.chain([first_row], reader)

            for row in reader:
                if not row or not row[0].strip():
                    continue

                url = row[0].strip()

                if is_blocked_url(url):
                    blocked += 1
                    continue

                conn.execute(
                    "INSERT OR IGNORE INTO authors (url) VALUES (?)",
                    (url,),
                )

                changes = conn.execute("SELECT changes()").fetchone()[0]
                if changes:
                    inserted += 1
                else:
                    skipped += 1

    logger.info("Ingest complete: %d new, %d already present, %d blocked", inserted, skipped, blocked)
    blocked_msg = f" {blocked} blocked domain(s) skipped." if blocked else ""
    print(f"Ingested {inserted} new author(s). ({skipped} already in database.{blocked_msg})")
    return 0


if __name__ == "__main__":
    raise SystemExit(ingest())
