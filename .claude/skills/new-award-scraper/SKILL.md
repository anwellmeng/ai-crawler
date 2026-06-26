---
name: new-award-scraper
description: This skill should be used when the user wants to "add a new award site", "scrape a new award", "implement a scraper for [award name]", "add [award site] to the pipeline", or "collect author URLs from [award site]". Use whenever the user brings a new award website they want to extract author website URLs from, even if they don't use those exact words. This skill produces a complete, standalone Python scraper script (like the existing award crawlers) that outputs a CSV ready for pipeline ingestion.
---

# New Award Scraper

Implement a standalone two-layer Python scraper for a new book award site. The output is a single `author_website_url`-column CSV that feeds into `pipeline.py ingest`.

## Inputs to collect from the user

Before writing any code, make sure you have:

1. **Award site URL** — the base URL (e.g. `https://independentpressaward.com`)
2. **Year range** — which years of winners to cover (start year, end year)
3. **Site structure description** — a brief description of how the site is laid out, if the user knows it (e.g. "each year has a `/winners/YYYY` page, clicking a winner goes to `/book/ID` which has the author website in the sidebar")

If the user only provides the URL and year range, that's enough to proceed — discover the structure yourself in the next step.

## Discover the site structure

Fetch the award site to understand its URL patterns before writing any code.

1. Fetch the base URL and a sample year listing page (e.g. `{base}/winners/2024` or whatever pattern the user described)
2. Identify: how are winner entries listed? What URL does each entry link to?
3. Fetch one winner detail page and identify: where is the author's personal website? (look for link text like "Author's Website", "Visit author", "Personal website", or the domain structure of linked URLs)
4. Check pagination: does the listing page paginate? How (page number in URL, offset parameter, "next" button)?
5. Note: does the site render content client-side (JS-heavy SPA)? If so, crawl4ai is needed instead of requests+BeautifulSoup

Summarize your findings to the user in 3–5 bullet points before writing code, so they can correct any misunderstandings.

## Ask the user about structure decisions

After confirming the site structure, ask how they want the script built. Keep this short — one or two questions:

- Should Phase 2 (extracting the author website from each winner page) be in the same script as Phase 1 (collecting winner links)? Almost always yes — only split if the user explicitly wants a two-step workflow.
- Any years to skip, or URL quirks the user knows about that aren't visible from the fetched pages?

## Implement the scraper

Write the script to `src/author_crawler/{site_name}_crawl.py` following these conventions exactly:

### Structure

Every award scraper is standalone — it runs independently, does not import from the pipeline, and outputs a CSV for the user to feed into `pipeline.py ingest`.

**Two phases in one file:**
- **Phase 1** — iterate year listing pages, collect all winner detail page URLs (internal award site URLs, not author sites)
- **Phase 2** — fetch each winner detail page concurrently, extract the author's personal website URL

### Code conventions

Read `references/code-patterns.md` for annotated boilerplate. The key invariants:

- Use `requests` + `BeautifulSoup` for plain HTML sites. Import `crawl4ai` only for JS-rendered SPAs.
- Filter blocked URLs using `is_blocked_url()` from `src/author_crawler/utils.py` — import it by adding `src/author_crawler/` to `sys.path` at the top of the script (same pattern as `scripts/crawl.py`). Never hardcode a domain blocklist in the scraper itself.
- Phase 2 must be concurrent. Use `concurrent.futures.ThreadPoolExecutor` for requests-based scrapers, or `asyncio` + `aiohttp`/`crawl4ai` for async scrapers.
- Output CSV: always include `author_website_url` as the first column. Add a second context column (e.g. `{site}_book_url`) if it's useful for debugging, but the pipeline only reads column 1.
- CLI via `argparse`: always include `--output` (default: `data/inputs/{site}_authors.csv`). Add `--start-year` / `--end-year` for year-range scrapers. Add `--input` or `--links-csv` if Phase 1 reads from a file.
- Print progress to stdout (e.g. `print(f"Phase 1: found {len(links)} winner links")`). No logging framework needed.
- Skip/warn on fetch errors rather than crashing — note the URL and continue.

### Filtering

After extracting a URL from a winner page, call `is_blocked_url(url)` and skip if it returns True. Also skip URLs that point back to the award site itself (the winner's profile page is not the author's personal site).

### Output

Write the CSV with `csv.writer`, header row first. Use `newline=""` when opening the file. After writing, print a one-line summary: `"Wrote N URLs to {output_path}"`.

## Test it

After writing the script, run it on a small range (1–2 years) and show the user the first few rows of output:

```bash
python3 src/author_crawler/{site_name}_crawl.py --start-year 2024 --end-year 2024 --output /tmp/test_out.csv
head -5 /tmp/test_out.csv
```

If it crashes or returns 0 rows, diagnose and fix before reporting done. Common issues:
- CSS selector or link-text match is wrong (inspect the actual page HTML)
- Site uses JS rendering — switch to crawl4ai
- Pagination differs from what was visible on the first page

## Update CLAUDE.md

After the script works, add a brief entry to the `## Commands` section in `CLAUDE.md` documenting how to run it, following the style of the existing entries. Include: what the script does, the key CLI flags, and the default output path.

## Additional Resources

- **`references/code-patterns.md`** — Annotated boilerplate skeleton with the two-phase pattern, argparse setup, concurrency, and CSV output
