#!/usr/bin/env python3
"""
Author contact extraction pipeline.

Usage
-----
  python pipeline.py ingest          Load authors.csv into the database
  python pipeline.py crawl           Crawl pending author websites
  python pipeline.py analyze         Analyze crawled markdown with LLM
  python pipeline.py export          Write results to CSV
  python pipeline.py run             Run all four stages in sequence
  python pipeline.py status          Show per-stage row counts
  python pipeline.py reset              Reset all rows to pending (keeps URLs)
  python pipeline.py reset --hard       Delete the database entirely
  python pipeline.py debug-analyze <url> Replay LLM call and print raw response
  python pipeline.py dump-md            Dump all markdown to disk
  python pipeline.py dump-md <url>      Dump one author's markdown to disk
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running from project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import LOGS_DIR
from db import get_conn, init_db


# ── Logging ───────────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    """
    Log to file only.  User-facing output uses print() so the terminal
    stays clean.  The log file captures everything for post-run auditing.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / "pipeline.log", encoding="utf-8"),
        ],
    )


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_ingest(_args: argparse.Namespace) -> int:
    from ingest import ingest
    return ingest()


def cmd_crawl(_args: argparse.Namespace) -> int:
    from crawl import crawl
    return asyncio.run(crawl())


def cmd_analyze(_args: argparse.Namespace) -> int:
    from analyze import analyze
    return asyncio.run(analyze())


def cmd_export(_args: argparse.Namespace) -> int:
    from export import export
    return export()


def cmd_run(_args: argparse.Namespace) -> int:
    """Run all stages in sequence, stopping on the first non-zero exit."""
    from ingest  import ingest
    from crawl   import crawl
    from analyze import analyze
    from export  import export

    stages = [
        ("ingest",  ingest,  False),
        ("crawl",   crawl,   True),
        ("analyze", analyze, True),
        ("export",  export,  False),
    ]

    for name, fn, is_async in stages:
        print(f"\n{'─' * 40}")
        print(f"  {name.upper()}")
        print(f"{'─' * 40}")
        rc = asyncio.run(fn()) if is_async else fn()
        if rc != 0:
            print(f"\nStage '{name}' exited with code {rc}. Pipeline aborted.")
            return rc

    print("\nPipeline complete.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]

        crawl_rows = conn.execute(
            "SELECT crawl_status, COUNT(*) AS n FROM authors GROUP BY crawl_status"
        ).fetchall()

        analyze_rows = conn.execute(
            """SELECT analyze_status, COUNT(*) AS n
               FROM   authors
               WHERE  crawl_status = 'crawled'
               GROUP  BY analyze_status"""
        ).fetchall()

    print(f"\nTotal authors: {total}")

    print("\nCrawl status:")
    for row in crawl_rows:
        print(f"  {row['crawl_status']:<20} {row['n']}")

    print("\nAnalyze status (crawled authors only):")
    if analyze_rows:
        for row in analyze_rows:
            print(f"  {row['analyze_status']:<20} {row['n']}")
    else:
        print("  (none crawled yet)")

    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    from config import DB_PATH
    if args.hard:
        if DB_PATH.exists():
            DB_PATH.unlink()
            print(f"Deleted {DB_PATH}.")
        else:
            print("No database found — nothing to delete.")
        return 0
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        conn.execute("""
            UPDATE authors
            SET crawl_status   = 'pending',
                analyze_status = 'pending',
                markdown       = NULL,
                emails         = NULL,
                contact_links  = NULL,
                crawl_error    = NULL,
                analyze_error  = NULL,
                updated_at     = datetime('now')
        """)
    print(f"Reset {n} row(s) to pending.")
    return 0


def cmd_debug_analyze(args: argparse.Namespace) -> int:
    """Replay the LLM call for a single URL and print the raw response."""
    import asyncio, os
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pathlib import Path
    from analyze import SYSTEM_PROMPT, _truncate, _token_count
    from config import LLM_CONCURRENCY, LLM_MAX_TOKENS, LLM_MODEL, LLM_TOKEN_LIMIT

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT url, markdown, analyze_status FROM authors WHERE url = ?",
            (args.url,),
        ).fetchone()

    if not row:
        print(f"URL not found in database: {args.url}")
        return 1

    print(f"analyze_status : {row['analyze_status']}")
    markdown = row["markdown"] or ""
    tok = _token_count(markdown)
    print(f"markdown tokens: {tok}")

    if tok > LLM_TOKEN_LIMIT:
        markdown = _truncate(markdown, LLM_TOKEN_LIMIT)
        print(f"(truncated to {LLM_TOKEN_LIMIT} tokens before sending)")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY is not set.")
        return 1

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    async def _call() -> str:
        completion = await client.chat.completions.create(
            model=LLM_MODEL,
            max_tokens=LLM_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": markdown},
            ],
        )
        return completion.choices[0].message.content

    print("\n── raw LLM response ──────────────────────────────────────────────")
    print(asyncio.run(_call()))
    print("──────────────────────────────────────────────────────────────────")
    return 0


def cmd_dump_md(args: argparse.Namespace) -> int:
    from export import dump_markdown
    return dump_markdown(args.url)


# ── CLI wiring ────────────────────────────────────────────────────────────────

_COMMANDS = {
    "ingest":   cmd_ingest,
    "crawl":    cmd_crawl,
    "analyze":  cmd_analyze,
    "export":   cmd_export,
    "run":      cmd_run,
    "status":   cmd_status,
    "reset":          cmd_reset,
    "debug-analyze":  cmd_debug_analyze,
    "dump-md":        cmd_dump_md,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Author contact extraction pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    sub.add_parser("ingest",  help="Load authors.csv into the database")
    sub.add_parser("crawl",   help="Crawl pending author websites")
    sub.add_parser("analyze", help="Analyze crawled markdown with LLM")
    sub.add_parser("export",  help="Write results to CSV")
    sub.add_parser("run",     help="Run all stages in sequence")
    sub.add_parser("status",  help="Show per-stage row counts")

    reset = sub.add_parser("reset", help="Reset pipeline state for a fresh run")
    reset.add_argument(
        "--hard",
        action="store_true",
        help="Delete the database entirely instead of resetting row statuses",
    )

    debug = sub.add_parser("debug-analyze", help="Replay LLM call for one URL and print raw response")
    debug.add_argument("url", help="Author URL to debug")

    dump = sub.add_parser("dump-md", help="Write markdown to disk for inspection")
    dump.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Author URL to dump (omit to dump all)",
    )

    return parser


def main() -> int:
    _configure_logging()
    init_db()   # no-op if schema already exists

    parser = _build_parser()
    args   = parser.parse_args()
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
