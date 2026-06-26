"""Project paths and configuration."""

from __future__ import annotations

from pathlib import Path
# Paths 
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"
LOGS_DIR = PROJECT_ROOT / "logs"

AUTHORS_CSV = INPUTS_DIR / "authors.csv"
AUTHORS_CONTACTS_CSV = OUTPUTS_DIR / "export.csv"
DB_PATH = DATA_DIR / "pipeline.db"
# -------
# Crawl Settings
CRAWL_CONCURRENCY = 10
CRAWL_MAX_DEPTH = 2
CRAWL_MAX_PAGES = 8
CRAWL_KEYWORDS = ["contact","email"]
CRAWL_KEYWORD_WEIGHT = 0.7
# -------
# Blocked domains — URLs on these domains are skipped at ingest and stripped
# from contact_links at export. Subdomains (e.g. m.facebook.com) are matched too.
BLOCKED_DOMAINS: frozenset = frozenset([
    # Amazon storefronts and shorteners
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au",
    "amazon.de", "amazon.fr", "amazon.es", "amazon.it",
    "amzn.to", "a.co",
    # Social media
    "facebook.com", "fb.com",
    "instagram.com",
    "twitter.com", "x.com",
    "linkedin.com",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "pinterest.com",
    # Book catalogues (not author contact pages)
    "goodreads.com",
])
# -------
# LLM Settings
LLM_MODEL       = "openai/gpt-oss-20b"
LLM_MAX_TOKENS  = 1_000
LLM_CONCURRENCY = 10       # simultaneous API calls
LLM_TOKEN_LIMIT = 16_000   # markdown truncated (top-kept) before sending. Tuned on 341 real crawled docs: p95 size ≈ 10K tokens, and 16K preserves the deepest extracted email/link in 99.6% of docs while capping the rare ~50K-token outlier. Lower limits lose tail recall (esp. /contact subpages appended after long homepages) for negligible savings, since cost is dominated by the many small docs no limit touches.
