"""
Ingest stage.

Reads the first column of authors.csv and inserts each URL into the
database.  INSERT OR IGNORE makes every run safe to re-run: existing
rows are left untouched so their crawl/analyze progress is preserved.
"""

from __future__ import annotations

import csv
import logging

from config import AUTHORS_CSV
from db import get_conn, init_db

logger = logging.getLogger(__name__)


def ingest() -> int:
    init_db()

    if not AUTHORS_CSV.exists():
        logger.error("Authors CSV not found: %s", AUTHORS_CSV)
        print(f"Error: {AUTHORS_CSV} not found.")
        return 1

    inserted = 0
    skipped = 0

    with AUTHORS_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None) # skip header
        with get_conn() as conn:
            for row in reader:
                if not row or not row[0].strip():
                    continue

                url = row[0].strip()

                conn.execute(
                    "INSERT OR IGNORE INTO authors (url) VALUES (?)",
                    (url,),
                )

                changes = conn.execute("SELECT changes()").fetchone()[0]
                if changes:
                    inserted += 1
                else:
                    skipped += 1

    logger.info("Ingest complete: %d new, %d already present", inserted, skipped)
    print(f"Ingested {inserted} new author(s). ({skipped} already in database.)")
    return 0