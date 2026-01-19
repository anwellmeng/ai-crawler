"""Project paths and configuration."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"
LOGS_DIR = PROJECT_ROOT / "logs"

AUTHORS_CSV = INPUTS_DIR / "authors.csv"
AUTHORS_CONTACTS_CSV = OUTPUTS_DIR / "export.csv"

JSONS_DIR = OUTPUTS_DIR / "jsons"
PROCESSED_JSONS_DIR = OUTPUTS_DIR / "processed_jsons"
FINISHED_SITES_DIR = OUTPUTS_DIR / "finished_sites"
SCRAPED_SITES_DIR = OUTPUTS_DIR / "scraped_sites"
FAILED_JSONS_DIR = OUTPUTS_DIR / "failed_jsons"
SKIPPED_SITES_DIR = OUTPUTS_DIR / "skipped_sites"
TO_ANALYZE_DIR = OUTPUTS_DIR / "to_analyze"
TEMP_DIR = OUTPUTS_DIR / "TEMP"
