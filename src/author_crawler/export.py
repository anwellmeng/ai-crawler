"""
Export stage.

Two responsibilities:
1. export()     — write a fresh CSV of all successfully analyzed authors.
2. dump_markdown() — write markdown from the DB back to disk for inspection.

The CSV is always written fresh (not appended) so re-running export
never produces duplicates or stale rows.  The URL column is included
so every row can be traced back to its source.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from pathlib import Path
from typing import Optional

from config import AUTHORS_CONTACTS_CSV, OUTPUTS_DIR
from db import get_conn
from utils import is_blocked_url

logger = logging.getLogger(__name__)


# ── CSV export ────────────────────────────────────────────────────────────────

def _filter_links(links_str: str | None) -> str:
    if not links_str:
        return ""
    return ";".join(l for l in links_str.split(";") if l and not is_blocked_url(l))


def export() -> int:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT url, emails, contact_links
               FROM authors
               WHERE analyze_status = 'done'"""
        ).fetchall()

    if not rows:
        print("No analyzed authors to export.")
        return 0

    AUTHORS_CONTACTS_CSV.parent.mkdir(parents=True, exist_ok=True)

    with AUTHORS_CONTACTS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "emails", "contact_links"])
        for row in rows:
            writer.writerow([
                row["url"],
                row["emails"] or "",
                _filter_links(row["contact_links"]),
            ])

    print(f"Exported {len(rows)} row(s) to {AUTHORS_CONTACTS_CSV}")
    logger.info("Exported %d rows to %s", len(rows), AUTHORS_CONTACTS_CSV)
    return 0


# ── Markdown dump (troubleshooting / on-demand) ───────────────────────────────

def dump_markdown(url: Optional[str] = None) -> int:
    """
    Write markdown back to disk for inspection.

    If `url` is given, dumps only that author.
    If `url` is None, dumps every author that has markdown in the DB.

    Files are written to data/outputs/md_dumps/{12-char url hash}.md
    so the name is deterministic and collision-free.
    """
    dump_dir = OUTPUTS_DIR / "md_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        if url:
            rows = conn.execute(
                "SELECT url, markdown FROM authors WHERE url = ?", (url,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT url, markdown FROM authors WHERE markdown IS NOT NULL"
            ).fetchall()

    if not rows:
        print("No matching rows found in database.")
        return 1

    for row in rows:
        slug    = hashlib.md5(row["url"].encode()).hexdigest()[:12]
        out     = dump_dir / f"{slug}.md"
        content = row["markdown"] or ""
        out.write_text(content, encoding="utf-8")
        print(f"  {row['url']}")
        print(f"    → {out}")

    print(f"\nDumped {len(rows)} file(s) to {dump_dir}/")
    return 0
