"""
Crawl stage.

Fetches all `pending` authors from the database and deep-crawls each
site concurrently, bounded by CRAWL_CONCURRENCY.  Results (combined
markdown or error) are written back to the DB row immediately so a
restart picks up exactly where it left off.

Design notes
------------
- Individual `arun` calls per author (rather than `arun_many`) let us
  group all sub-pages from a single site into one markdown blob and
  attribute it to the correct author URL. (Will look into  more efficient methods later)
- A semaphore gates concurrent browser sessions; crawl4ai's internal
  dispatcher still handles per-domain rate limiting.
- DB writes happen sequentially in the main coroutine after each
  `as_completed` yield, so no concurrent write contention.
"""
from __future__ import annotations

import asyncio, logging
from typing import Optional

from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
from config import(
    CRAWL_CONCURRENCY,
    CRAWL_KEYWORDS,
    CRAWL_KEYWORD_WEIGHT,
    CRAWL_MAX_DEPTH,
    CRAWL_MAX_PAGES
)

from db import get_conn

logger = logging.getLogger(__name__)

# ── Per-author crawl ──────────────────────────────────────────────────────────
 
async def _crawl_one(
    crawler: AsyncWebCrawler,
    url: str,
    run_config: CrawlerRunConfig,
    semaphore: asyncio.Semaphore,
) -> tuple[str, Optional[str], Optional[str]]:
    """
    Returns (url, markdown, error).
    Exactly one of markdown / error will be None.
    """
    async with semaphore:
        try:
            results = await crawler.arun(url, config=run_config)
            pages = [r for r in results if r.success and r.markdown]
            if not pages:
                return url, None, "No successful pages returned"
            combined = "\n\n".join(r.markdown for r in pages)
            return url, combined, None
        except Exception as exc:
            return url, None, str(exc)
# ── DB writes (called from main coroutine, no concurrency) ────────────────────
 
def _mark_crawled(url: str, markdown: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE authors
               SET crawl_status = 'crawled',
                   markdown     = ?,
                   crawl_error  = NULL,
                   updated_at   = datetime('now')
               WHERE url = ?""",
            (markdown, url),
        )
 
 
def _mark_crawl_failed(url: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE authors
               SET crawl_status = 'failed',
                   crawl_error  = ?,
                   updated_at   = datetime('now')
               WHERE url = ?""",
            (error, url),
        )
     
# ── Stage entry point ─────────────────────────────────────────────────────────          
async def crawl() -> int:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT url FROM authors WHERE crawl_status = 'pending'"
        ).fetchall()

    urls = [r["url"] for r in rows]
    if not urls:
        print("No pending URLs to crawl.")
        return 0

    print(f"Crawling {len(urls)} author(s) (concurrency={CRAWL_CONCURRENCY})...")

    # Inlined from _make_run_config — only called once
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=BestFirstCrawlingStrategy(
            max_depth=CRAWL_MAX_DEPTH,
            include_external=False,
            url_scorer=KeywordRelevanceScorer(
                keywords=CRAWL_KEYWORDS,
                weight=CRAWL_KEYWORD_WEIGHT,
            ),
            max_pages=CRAWL_MAX_PAGES,
        )
        # DefaultMarkdownGenerator removed — redundant
    )

    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)
    succeeded = 0
    failed    = 0

    async with AsyncWebCrawler(config=BrowserConfig()) as crawler:
        tasks = [
            asyncio.create_task(_crawl_one(crawler, url, run_config, semaphore))
            for url in urls
        ]
        for coro in asyncio.as_completed(tasks):
            url, markdown, error = await coro
            if error:
                _mark_crawl_failed(url, error)
                failed += 1
            else:
                _mark_crawled(url, markdown)
                succeeded += 1

            completed = succeeded + failed
            if completed % 100 == 0:
                print(f"  {completed}/{len(urls)} complete …")

    print(f"Crawl complete: {succeeded} succeeded, {failed} failed.")
    return 0