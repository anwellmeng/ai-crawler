"""
Thin SQLite layer.

All pipeline state lives in a single `authors` table.  Every stage
reads the status columns it cares about and writes only its own.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from config import DB_PATH


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a WAL-mode connection that auto-commits on clean exit
    and rolls back on exception.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for repeated opens
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create schema if it doesn't exist.  Safe to call on every startup."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS authors (
                url              TEXT PRIMARY KEY,

                -- crawl stage
                crawl_status     TEXT NOT NULL DEFAULT 'pending',
                markdown         TEXT,
                crawl_error      TEXT,

                -- analyze stage
                analyze_status   TEXT NOT NULL DEFAULT 'pending',
                analyze_error    TEXT,
                emails           TEXT,   -- semicolon-separated
                contact_links    TEXT,   -- semicolon-separated

                -- ingestion batch: stamped by ingest, scopes export to a single run
                batch_id         INTEGER,

                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migrate pre-batch databases: add the column and fold legacy rows into
        # batch 0 so the first new ingest becomes batch 1+ and export can scope.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(authors)")}
        if "batch_id" not in columns:
            conn.execute("ALTER TABLE authors ADD COLUMN batch_id INTEGER")
        conn.execute("UPDATE authors SET batch_id = 0 WHERE batch_id IS NULL")
        # Indexes on status columns so stage queries stay fast at 6k rows.
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_crawl_status
            ON authors (crawl_status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyze_status
            ON authors (analyze_status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_batch_id
            ON authors (batch_id)
        """)