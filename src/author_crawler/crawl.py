"""
Crawl stage.

Fetches all `pending` authors from the database and deep-crawls each
site concurrently, bounded by CRAWL_CONCURRENCY.  Results (combined
markdown or error) are written back to the DB row immediately so a
restart picks up exactly where it left off.

Design notes
------------
- We use a shared AsyncWebCrawler (one browser for all authors) with
  simple arun() calls — no BestFirstCrawlingStrategy. That strategy
  spawns its own internal crawlers per sub-page, causing an uncontrolled
  browser-instance explosion at scale.
- Per-author logic: crawl the root page, extract internal links whose
  URL path or anchor text matches contact/email/about keywords, then
  crawl up to CRAWL_MAX_PAGES-1 of those sequentially.
- A semaphore gates concurrent author sessions. Sub-page fetches happen
  inside the same semaphore slot, so concurrent page count ≤ CRAWL_CONCURRENCY.
- DB writes happen sequentially in the main coroutine after each
  as_completed yield, so no concurrent write contention.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from config import (
    CRAWL_CONCURRENCY,
    CRAWL_KEYWORDS,
    CRAWL_MAX_PAGES,
)

from db import get_conn

logger = logging.getLogger(__name__)

_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in CRAWL_KEYWORDS), re.IGNORECASE)


# ── Link extraction ───────────────────────────────────────────────────────────

def _contact_links(result, base_url: str, limit: int) -> list[str]:
    """
    Return up to `limit` internal URLs from a CrawlResult whose path or
    link text matches CRAWL_KEYWORDS.
    """
    internal = result.links.get("internal", [])
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[str] = []
    for link in internal:
        href = link.get("href", "").strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Same host, no fragment-only links
        if parsed.netloc != base_host:
            continue
        # Strip fragment for dedup; normalize trailing slash for base comparison
        full_clean = parsed._replace(fragment="").geturl()
        if full_clean in seen or full_clean.rstrip("/") == base_url.rstrip("/"):
            continue
        text = link.get("text", "")
        if _KEYWORD_RE.search(parsed.path) or _KEYWORD_RE.search(text):
            seen.add(full_clean)
            out.append(full_clean)
        if len(out) >= limit:
            break
    return out


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
            pages: list[str] = []

            root = await crawler.arun(url, config=run_config)
            if root.success and root.markdown:
                pages.append(root.markdown)

            if root.success:
                sub_urls = _contact_links(root, url, limit=CRAWL_MAX_PAGES - 1)
                for sub_url in sub_urls:
                    sub = await crawler.arun(sub_url, config=run_config)
                    if sub.success and sub.markdown:
                        pages.append(sub.markdown)

            if not pages:
                return url, None, "No successful pages returned"
            return url, "\n\n".join(pages), None
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

    run_config = CrawlerRunConfig()

    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)
    succeeded = 0
    failed = 0

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
