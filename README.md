# AI Crawler

The **AI Crawler** is a small Python utility that scrapes unique author websites, extracts contact information, and compiles the results into a CSV file. It is designed to help writers, publishers, and researchers gather email addresses and contact form links from a list of author URLs—even when each site is structured differently.

## Stack
**Programming Language:** Python 3.11+

**Libraries:**
* openai – Python API client library (used with OpenRouter via a custom base URL, or with OpenAI directly).
* crawl4ai – asynchronous web crawler that fetches pages and generates Markdown.
* tiktoken – token counter for limiting API usage.
* csv, json, re, shutil, pathlib – standard library modules.

**External Services / APIs:**

* **OpenRouter (default)** – model API provider used for LLM-based extraction of emails and contact links.
* **OpenAI (optional)** – can be used instead by configuring the client to point to OpenAI’s endpoint and using an OpenAI API key.

## Project Structure
```
README.md              # Project overview and usage
requirements.txt       # Python dependencies
author_crawler/          # Core library
    __init__.py
    analyze.py            # Uses an LLM to parse scraped Markdown
    crawl.py              # Crawls websites and saves Markdown
    export.py             # Converts JSON results to a CSV
    config.py             # Shared paths and constants
scripts/
    crawl.py              # CLI wrapper for crawling
    analyze.py            # CLI wrapper for analysis
    export_csv.py         # CLI wrapper for exporting
data/
    inputs/
        authors.csv       # CSV of author URLs
    outputs/
        jsons/            # Raw JSON from OpenAI
        processed_jsons/  # Moved after export
        authors_contacts.csv  # Final CSV
        skipped_sites/    # Moved if markdown file is too large
        failed_jsons/     # Any sites that couldn't be scanned
```

## Prerequisites
* **Python 3.11+** – The code uses type annotations and modern syntax.
* **API key**
  * Default: set the environment variable `OPENROUTER_API_KEY` (OpenRouter).
  * Optional: set `OPENAI_API_KEY` and configure the code to use OpenAI’s endpoint instead.
* **Python packages** – Install the dependencies:

```bash
pip install -r requirements.txt
```

If a `requirements.txt` is not present, install the following packages manually:

```bash
pip install openai crawl4ai tiktoken
```

## Usage

1. **Prepare the input** – Place a CSV file named `authors.csv` in `data/inputs/`. The CSV should have a header row and a column containing the author website URLs. **The crawler reads URLs from the first column only, so make sure the URLs are in column 1.**

2. **Crawl the websites** – Run the crawler script:

```bash
python scripts/crawl.py
```

The crawler will create Markdown files in `data/outputs/scraped_sites/`.

3. **Analyze the scraped content** – Process the Markdown files with OpenAI to extract emails and contact links:

```bash
python scripts/analyze.py
```

The results are stored as JSON files in `data/outputs/jsons/`.

4. **Export to CSV** – Convert the JSON results into a single CSV file:

```bash
python scripts/export_csv.py
```

The final CSV `authors_contacts.csv` will be located in `data/outputs/`.

## Notes

* The project relies on the requirements.txt file or the manual install instructions in the README; exact package versions are not pinned.
* The crawler limits each page to 122 000 tokens to avoid excessive API usage.
* Files that exceed the token limit are moved to `skipped_sites/`.
* Failed JSON responses are moved to `failed_jsons/` for debugging.
* OpenRouter's free models tend to get rate limited when there's high usage. You can switch the model out in crawl.py with any other openrouter model of your liking. The default is **meituan/longcat-flash-chat:free**, a free model on OpenRouter.
* The project is intended to run locally on a machine with Python 3.11+ and internet access to the target websites and OpenAI API.

## Disclaimer
This tool is intended for responsible use. Always respect websites’ terms of service, robots.txt, and applicable data privacy laws when collecting contact information.

## Lessons Learned (so far):
* I learned that over-planning was slowing me down. Once I started writing code, the project’s direction became clearer with each iteration, helping me focus on progress instead of perfection.
