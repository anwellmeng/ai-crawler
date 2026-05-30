import csv
from pathlib import Path

AUTHOR_URLS = [
    "https://example.com",
    "https://example.org"
]


def make_authors_csv(path: Path) -> Path:
    """Write AUTHOR_URLS to a CSV at the given path and return it."""
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        for url in AUTHOR_URLS:
            writer.writerow([url])
    return path
