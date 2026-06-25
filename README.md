# AI Crawler

A four-stage Python pipeline that takes a CSV of website URLs, deep-crawls each site, uses an LLM to extract contact information, and outputs a single CSV of emails and contact form links.

All pipeline state is stored in a local SQLite database (`data/pipeline.db`), so runs are resumable — if the process is interrupted, re-running picks up exactly where it left off without re-crawling or re-analyzing completed rows.

---

## Prerequisites

- Python 3.11+
- An [OpenRouter](https://openrouter.ai) API key (free tier works)

---

## Setup

**1. Clone the repository and install dependencies:**

```bash
pip install -r requirements.txt
```

**2. Create a `.env` file in the project root with your API key:**

```
OPENROUTER_API_KEY=your_key_here
```

**3. Prepare your input CSV:**

Create `data/inputs/authors.csv` with one URL per row in the first column. A header row is optional — the pipeline detects and skips it automatically.

```
https://www.authorname.com/
https://www.anotherauthor.com/
https://www.writerswebsite.org/
```

---

## Running the Pipeline

**Run all four stages in sequence:**

```bash
python src/author_crawler/pipeline.py run
```

**Check progress at any time:**

```bash
python src/author_crawler/pipeline.py status
```

The output CSV is written to `data/outputs/export.csv` when the pipeline finishes. By default it contains only the sites from your **most recent ingestion** — the URLs in the input file you just ran. To include every site analyzed across all past runs, add `--all`:

```bash
python src/author_crawler/pipeline.py run --all
```

---

## Stages

The pipeline has four stages that can also be run individually:

| Command | What it does |
|---|---|
| `ingest` | Loads `authors.csv` into the database and tags every URL in the file with a new ingestion batch. Safe to re-run — crawl/analyze progress is never touched. |
| `crawl` | Deep-crawls each site and stores the combined page Markdown in the DB. |
| `analyze` | Sends each site's Markdown to the LLM and extracts emails and contact form links. |
| `export` | Writes the most recent ingestion's analyzed rows to `data/outputs/export.csv`. Add `--all` (`-a`) to export every analyzed row across all past ingestions. |
| `reset` | Resets all row statuses to `pending` so the full pipeline re-runs on the same URLs. |
| `reset --hard` | Deletes the database entirely for a completely clean slate. |

```bash
python src/author_crawler/pipeline.py ingest
python src/author_crawler/pipeline.py crawl
python src/author_crawler/pipeline.py analyze
python src/author_crawler/pipeline.py export        # latest ingestion only
python src/author_crawler/pipeline.py export --all  # every analyzed row
```

---

## Output

`data/outputs/export.csv` has three columns:

| Column | Description |
|---|---|
| `url` | The original author URL from your input CSV |
| `emails` | Semicolon-separated email addresses found on the site |
| `contact_links` | Semicolon-separated URLs of contact form pages |

Example:

```
url,emails,contact_links
https://www.authorname.com/,author@example.com,https://www.authorname.com/contact
https://www.anotherauthor.com/,,https://www.anotherauthor.com/connect
```

---

## Troubleshooting

**Inspect what the crawler captured for a specific site:**

```bash
python src/author_crawler/pipeline.py dump-md https://www.authorname.com/
```

This writes the stored Markdown to `data/outputs/md_dumps/` so you can see exactly what the LLM received.

**Dump all stored Markdown at once:**

```bash
python src/author_crawler/pipeline.py dump-md
```

**Start a fresh run on the same URLs:**

```bash
python src/author_crawler/pipeline.py reset
python src/author_crawler/pipeline.py run
```

Use `reset --hard` instead to also wipe the URL list (you'll need to re-ingest `authors.csv`).

**Re-analyze a failed row** — update its status directly in the database, then re-run analyze:

```bash
sqlite3 data/pipeline.db \
  "UPDATE authors SET analyze_status='pending' WHERE url='https://www.authorname.com/'"
python src/author_crawler/pipeline.py analyze
```

---

## Configuration

All tunable settings are in `src/author_crawler/config.py`:

```python
# Crawl settings
CRAWL_CONCURRENCY = 20      # simultaneous browser sessions
CRAWL_MAX_DEPTH   = 2       # how many link-hops deep to follow
CRAWL_MAX_PAGES   = 8       # max pages fetched per site

# LLM settings
LLM_MODEL         = "openai/gpt-oss-20b"   # any OpenRouter model ID
LLM_CONCURRENCY   = 10      # simultaneous API calls
LLM_TOKEN_LIMIT   = 128_000 # markdown is truncated to this before sending
```

To switch LLM models, replace `LLM_MODEL` with any model available on [OpenRouter](https://openrouter.ai/models). Free-tier models may be rate-limited under heavy load — a paid model will be more reliable for large batches.

---

## Project Structure

```
data/
    inputs/
        authors.csv          # Your input URLs (one per row, first column)
    outputs/
        export.csv           # Final output
        md_dumps/            # Markdown debug dumps (generated on demand)
    pipeline.db              # SQLite database (all pipeline state)
logs/
    pipeline.log             # Full run log for debugging
src/author_crawler/
    pipeline.py              # CLI entry point
    ingest.py                # Stage 1: CSV -> DB
    crawl.py                 # Stage 2: crawl sites, store Markdown
    analyze.py               # Stage 3: LLM extraction
    export.py                # Stage 4: DB -> CSV
    config.py                # Paths and tunable parameters
    db.py                    # SQLite connection and schema
    awards_crawl.py          # Standalone NIEA finalist page scraper (separate tool)
tests/
    unit/                    # Per-module unit tests
    integration/             # Full pipeline integration tests
```

---

## Notes

- The pipeline is resumable. If a crawl is interrupted, re-running `crawl` only processes URLs still marked `pending`.
- Sites that return very little content (bot-blocked, login-walled, blank pages) are marked `skipped` in the analyze stage and are not included in the output.
- The crawler respects per-domain rate limiting via crawl4ai's internal dispatcher.
- All logging goes to `logs/pipeline.log`. The terminal only shows progress summaries.

---

## Disclaimer

Use this tool responsibly. Always respect a website's `robots.txt`, terms of service, and applicable data privacy laws when collecting contact information.
