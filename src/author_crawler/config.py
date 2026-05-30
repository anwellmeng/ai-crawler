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
# LLM Settings
LLM_MODEL       = "openai/gpt-oss-20b"
LLM_MAX_TOKENS  = 1_000
LLM_CONCURRENCY = 10       # simultaneous API calls
LLM_TOKEN_LIMIT = 128_000  # markdown truncated to this before sending (model ctx=131K, ~3K reserved for system prompt + output)
