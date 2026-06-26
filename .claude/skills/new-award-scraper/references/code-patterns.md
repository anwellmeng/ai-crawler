# Award Scraper Code Patterns

Annotated boilerplate for a two-phase award site scraper. Copy and adapt — every section is intentional.

## Full skeleton

```python
"""Scrape author website URLs from {Award Name} ({base_url}).

Phase 1: Iterate year listing pages to collect winner detail page URLs.
Phase 2: Fetch each winner page and extract the author's personal website.
Output: single-column CSV (author_website_url) for pipeline ingestion.
"""
from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Add src/author_crawler/ to path so utils.py is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "author_crawler"))
from utils import is_blocked_url  # noqa: E402

BASE_URL = "https://example-award-site.com"
DEFAULT_OUTPUT = ROOT / "data" / "inputs" / "example_authors.csv"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
PHASE2_WORKERS = 8  # concurrent fetches in Phase 2


# ---------------------------------------------------------------------------
# Phase 1 — collect winner detail page URLs
# ---------------------------------------------------------------------------

def collect_winner_links(start_year: int, end_year: int) -> list[str]:
    """Return all winner detail page URLs across the given year range."""
    links: list[str] = []
    for year in range(start_year, end_year - 1, -1):
        year_links = _scrape_year(year)
        print(f"  {year}: {len(year_links)} winners")
        links.extend(year_links)
    print(f"Phase 1 complete: {len(links)} total winner links")
    return links


def _scrape_year(year: int) -> list[str]:
    """Return winner detail URLs for a single year. Handles pagination."""
    url = f"{BASE_URL}/winners/{year}/"
    found: list[str] = []
    page = 1
    while True:
        page_url = url if page == 1 else f"{url}?page={page}"
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Warning: failed to fetch {page_url}: {e}")
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        # Adapt this selector to the actual site structure
        entries = soup.select("a.winner-entry")
        if not entries:
            break
        for a in entries:
            href = a.get("href", "")
            if href and not href.startswith("http"):
                href = BASE_URL.rstrip("/") + "/" + href.lstrip("/")
            if href:
                found.append(href)
        # Stop if no "next page" link
        if not soup.select_one("a.next-page"):
            break
        page += 1
    return found


# ---------------------------------------------------------------------------
# Phase 2 — extract author website from each winner page
# ---------------------------------------------------------------------------

def extract_author_websites(winner_links: list[str]) -> list[tuple[str, str]]:
    """Return (author_website_url, winner_page_url) pairs. Concurrent."""
    results: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=PHASE2_WORKERS) as pool:
        futures = {pool.submit(_fetch_author_url, link): link for link in winner_links}
        for future in as_completed(futures):
            author_url = future.result()
            if author_url:
                results.append((author_url, futures[future]))
    print(f"Phase 2 complete: {len(results)} author websites found")
    return results


def _fetch_author_url(winner_url: str) -> str | None:
    """Fetch a winner page and return the author's personal website URL, or None."""
    try:
        resp = requests.get(winner_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Warning: failed to fetch {winner_url}: {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    # Adapt: find the link whose text or label indicates the author's website
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"]
        if "author" in text and "website" in text:
            return _normalize(href, winner_url)
    return None


def _normalize(href: str, page_url: str) -> str | None:
    """Expand relative URLs and filter blocked/internal domains."""
    if not href or href.startswith("#") or href.startswith("mailto:"):
        return None
    if not href.startswith("http"):
        from urllib.parse import urljoin
        href = urljoin(page_url, href)
    if is_blocked_url(href):
        return None
    # Skip links that point back to the award site itself
    if BASE_URL.split("//")[-1].split("/")[0] in href:
        return None
    return href


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_csv(rows: list[tuple[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["author_website_url", "winner_page_url"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} URLs to {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape author URLs from {Award Name}")
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2020)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print(f"Phase 1: collecting winner links ({args.start_year}–{args.end_year})")
    winner_links = collect_winner_links(args.start_year, args.end_year)

    print("Phase 2: extracting author websites")
    rows = extract_author_websites(winner_links)

    write_csv(rows, args.output)


if __name__ == "__main__":
    main()
```

## Key adaptation points

When implementing a real scraper, these are the things that change per site:

| Variable | What to adapt |
|---|---|
| `BASE_URL` | The award site's base domain |
| `f"{BASE_URL}/winners/{year}/"` | The actual year-listing URL pattern |
| `soup.select("a.winner-entry")` | CSS selector for winner links on the listing page |
| `soup.select_one("a.next-page")` | Pagination detection — could be offset, page number, or absent |
| `"author" in text and "website" in text` | Link text heuristic for the author website on the winner page |
| `PHASE2_WORKERS` | Reduce if the site rate-limits aggressively |

## When to use crawl4ai instead of requests

If the site's listing or winner pages are a JavaScript SPA (content doesn't appear in `view-source:`), use crawl4ai with a post-load delay:

```python
from crawl4ai import AsyncWebCrawler

async def fetch_js_page(url: str) -> str:
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, js_code=None, wait_for=3.0)
        return result.html or ""
```

Then parse `result.html` with BeautifulSoup as normal. Run with `asyncio.run(main())`.

## File input pattern (when Phase 1 is pre-done)

Some scrapers skip Phase 1 entirely and take a file of links as input (like `naiwe_crawl.py`). Use this argparse pattern:

```python
parser.add_argument("--input", type=Path, required=True,
                    help="CSV or text file with one URL per line (or single-column CSV)")
```

Read with:
```python
with open(args.input) as f:
    reader = csv.reader(f)
    next(reader, None)  # skip header if present
    links = [row[0] for row in reader if row]
```

## Offset-based pagination pattern

Some sites use `/offset=N` instead of `?page=N`:

```python
offset = 0
step = None  # auto-detected from first page
while True:
    page_url = f"{listing_url}/offset={offset}" if offset else listing_url
    # ... fetch and parse ...
    entries = soup.select("a.book-entry")
    if not entries:
        break
    # Detect step from first page
    if step is None:
        step = len(entries)
    if len(entries) < step:
        break  # last page
    offset += step
```
