# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (Python 3.11+ required)
pip install -r requirements.txt

# Run the full pipeline (default input: data/inputs/authors.csv)
python src/author_crawler/pipeline.py run
# Run with a specific input file
python src/author_crawler/pipeline.py run --input data/inputs/my_authors.csv

# Run individual stages
python src/author_crawler/pipeline.py ingest
# Ingest a specific file
python src/author_crawler/pipeline.py ingest --input data/inputs/my_authors.csv
python src/author_crawler/pipeline.py crawl
python src/author_crawler/pipeline.py analyze
python src/author_crawler/pipeline.py export

# Check pipeline progress
python src/author_crawler/pipeline.py status

# Reset all rows to pending for a fresh run (keeps URLs)
python src/author_crawler/pipeline.py reset
# Delete the database entirely
python src/author_crawler/pipeline.py reset --hard

# Reconstruct markdown from DB for a specific URL (troubleshooting)
python src/author_crawler/pipeline.py dump-md <url>
python src/author_crawler/pipeline.py dump-md   # dumps all

# Replay the LLM call for one URL and print the raw JSON response (troubleshooting)
python src/author_crawler/pipeline.py debug-analyze <url>

# Scrape NIEA finalist pages to build authors.csv (standalone, not part of main pipeline)
# --start/--end are two-digit year suffixes in descending order (e.g. 19=2019, 13=2013)
python src/author_crawler/awards_crawl.py --start 19 --end 13 --output data/inputs/authors.csv

# Scrape author website URLs from the Mom's Choice Awards store (standalone)
# Outputs a single-column CSV ready to feed into pipeline.py ingest
python src/author_crawler/mca_store_crawl.py --output data/inputs/mca_store_authors.csv
# Override the category URL (default is category/2 — Young Adult books)
python src/author_crawler/mca_store_crawl.py --category-url https://store.momschoiceawards.com/category/3

# Scrape author website URLs from The Book Fest (standalone)
# Crawls all seasons (spring + fall) from --end-year through --start-year
# Books without an "Author's Website" link are skipped automatically
python src/author_crawler/bookfest_crawl.py --output data/inputs/bookfest_authors.csv
# Crawl only a specific year range
python src/author_crawler/bookfest_crawl.py --start-year 2026 --end-year 2020

# Scrape author/publisher website URLs from IBPA Book Awards category pages (standalone)
# Reads categories.csv (Category, URL) and extracts "Click to Purchase" links;
# Amazon and social media links are automatically filtered out
python src/author_crawler/ibpa_crawl.py --output data/inputs/ibpa_authors.csv
# Use a custom categories CSV
python src/author_crawler/ibpa_crawl.py --categories-csv categories.csv --output data/inputs/ibpa_authors.csv

# Scrape individual book page URLs from the Book Excellence Awards honorees site (standalone)
# Reads links.csv (one listing URL per line), paginates via /offset=N, collects all
# individual book page links (#!/.../p/ID/category=ID)
python src/author_crawler/bea_crawl.py --output data/inputs/bea_authors.csv
# Use a custom links file
python src/author_crawler/bea_crawl.py --links-csv links.csv --output data/inputs/bea_authors.csv

# Scrape author name, title, and category from the Independent Press Award (standalone)
# Covers 2017–2022 winners and distinguished favorites; outputs two separate CSVs
# each with year, category, title, author columns (NOT pipeline URL collectors)
python src/author_crawler/ipa_crawl.py
python src/author_crawler/ipa_crawl.py \
    --winners-output data/inputs/ipa_winners.csv \
    --distinguished-output data/inputs/ipa_distinguished.csv

# Scrape author website URLs from NAIWE book review pages (standalone)
# Reads a list of review page URLs, extracts the "Author: [Name](URL)" link from each
# Amazon and social media links are automatically filtered out
python src/author_crawler/naiwe_crawl.py --input data/inputs/naiwe_authors.csv --output data/inputs/naiwe_out.csv

# Run all tests
python -m pytest tests/

# Run a single test file or test case
python -m pytest tests/unit/test_crawl.py
python -m pytest tests/unit/test_crawl.py::TestCrawl::test_crawl_stores_markdown_in_db
```

Env vars required: `OPENROUTER_API_KEY` (set in `.env`, loaded by `python-dotenv`).

## Architecture

Four-stage pipeline: a CSV of author website URLs goes in, a CSV of contact information (emails + contact form links) comes out. All inter-stage state lives in a single SQLite DB (`data/pipeline.db`) — there are no intermediate file directories.

**Stages (in order):**

1. **Ingest** (`ingest.py`) — reads the first column of `data/inputs/authors.csv` and upserts each URL via `INSERT OR IGNORE`. Reruns are always safe; existing rows and their progress are never touched.

2. **Crawl** (`crawl.py`) — reads `crawl_status='pending'` rows, concurrently deep-crawls each site using `crawl4ai`'s `BestFirstCrawlingStrategy` with `KeywordRelevanceScorer` (keywords: `contact`, `email`), bounded by `asyncio.Semaphore(CRAWL_CONCURRENCY)`. Uses `asyncio.as_completed` for streaming. Writes combined per-author Markdown as a TEXT column directly to the DB row. DB writes are sequential in the main coroutine to avoid write contention.

3. **Analyze** (`analyze.py`) — reads rows where `crawl_status='crawled' AND analyze_status='pending'`, sends Markdown to the LLM via `AsyncOpenAI` pointed at OpenRouter's base URL. Bounded by `asyncio.Semaphore(LLM_CONCURRENCY)`. Writes `emails` and `contact_links` as semicolon-separated strings back to the DB. Status transitions: `pending → done | failed | skipped` (skipped if token count exceeds `LLM_TOKEN_LIMIT`).

4. **Export** (`export.py`) — reads `analyze_status='done'` rows and writes a fresh CSV (always write mode, never append) to `data/outputs/export.csv` with three columns: `url`, `emails`, `contact_links`. Also provides `dump_markdown(url=None)` to reconstruct `.md` files from DB on demand, written to `data/outputs/md_dumps/{md5(url)[:12]}.md`.

**Entry point** (`pipeline.py`) — single CLI tying all stages together. Commands: `ingest`, `crawl`, `analyze`, `export`, `run` (all four in sequence), `status` (per-stage row counts), `dump-md`. Calls `init_db()` at startup (idempotent). Logging goes to `logs/pipeline.log` (file only); user-facing progress uses `print()`.

**DB schema** (`db.py`) — single `authors` table, `url` as PRIMARY KEY. `crawl_status` and `analyze_status` columns are the only inter-stage communication. Additional columns: `markdown` (TEXT), `crawl_error`, `analyze_error`, `emails`, `contact_links` (semicolons), `created_at`, `updated_at`. `get_conn()` is the sole DB access point (WAL mode, `row_factory=sqlite3.Row`, auto-commit/rollback context manager). `init_db()` is safe to call on every startup.

**Config** (`config.py`) — all paths and tunable parameters in one place, no logic. Imported as `from config import ...` (bare module name), so all stage scripts must be run with `src/author_crawler/` on `sys.path` — the `pipeline.py` entry point handles this automatically. Crawl settings (`CRAWL_CONCURRENCY`, `CRAWL_MAX_DEPTH`, `CRAWL_MAX_PAGES`, `CRAWL_KEYWORDS`, `CRAWL_KEYWORD_WEIGHT`) and LLM settings (`LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_CONCURRENCY`, `LLM_TOKEN_LIMIT`) live here.

**`awards_crawl.py`** is a standalone scraper for the National Indie Excellence Awards finalist pages. It is not part of the main pipeline.

**`mca_store_crawl.py`** is a standalone two-phase scraper for the Mom's Choice Awards store. Phase 1 collects all product page URLs from the category listing (tries a single bulk `?limit=1000` request first, falls back to pagination). Phase 2 fetches each product page concurrently and extracts the `Website:` field using BeautifulSoup. Outputs a single-column `author_website_url` CSV to feed into the pipeline.

**`bookfest_crawl.py`** is a standalone two-phase scraper for The Book Fest (`thebookfest.com`). Phase 1 iterates all season listing pages (`books-{spring|fall}-{year}/`) from `--start-year` down to `--end-year`, paginating each until a 404 or empty page. Phase 2 fetches each book detail page concurrently and extracts the "Author's Website" link (identified by link text; books without a valid external href are skipped). Outputs a two-column CSV (`author_website_url`, `bookfest_book_url`) — pipeline reads the first column only.

**`ibpa_crawl.py`** is a standalone single-phase scraper for the IBPA Book Awards (`ibpabookaward.org`). Reads `categories.csv` (Category, URL columns) and for each category page extracts all "Click to Purchase" links that point to external author or publisher sites. Amazon, social media, and other non-author domains are filtered out. Outputs a two-column CSV (`author_website_url`, `ibpa_category_url`) — pipeline reads the first column only.

**`bea_crawl.py`** is a standalone single-phase scraper for the Book Excellence Awards honorees site (`honorees.bookexcellenceawards.com`). Reads `links.csv` (one listing URL per line) and paginates through each using `/offset=N` URLs. The offset step is auto-detected from the first page. Content is JS-rendered (hash-based SPA), so crawl4ai uses a 3-second post-load delay to allow the SPA to render. Outputs a single-column CSV (`book_url`) of individual book honoree page URLs — pipeline reads the first column.

**`ipa_crawl.py`** is a standalone **data extraction** script (not a URL collector) for the Independent Press Award (`independentpressaward.com`). Scrapes 12 pages (winners + distinguished favorites for 2017–2022) and extracts structured book records — author name, title, category — anchored on the "by [Author]" attribution line in the markdown output. Outputs two CSVs: `ipa_winners.csv` and `ipa_distinguished.csv`, each with columns `year, category, title, author`.

**`naiwe_crawl.py`** is a standalone single-phase scraper for NAIWE book review pages (`news.naiwe.org`). Reads a list of review page URLs (one per line or single-column CSV), fetches each page, and extracts the author's website from the `Author: [Name](URL)` link present in every review. Amazon, social media, and naiwe.com URLs are filtered out. Outputs a single-column CSV (`author_website_url`) to feed into the pipeline.

## Testing

Tests use `unittest` with `patch.object(db, "DB_PATH", tmp_path)` to redirect all DB access to a temp file — every test that touches the DB must apply this patch. Integration test fixtures (`AUTHOR_URLS`, `make_authors_csv`) live in `tests/integration/__init__.py`.

The `scripts/` directory contains pre-refactor file-based scripts and is not part of the current pipeline.
