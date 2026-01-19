from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

from config import(
    PROCESSED_JSONS_DIR,
    JSONS_DIR,
    AUTHORS_CONTACTS_CSV,
)

def is_url_like_filename(filename: str) -> bool:
    """Check if filename looks like a URL (contains http and ends with domain-like pattern)"""
    # Remove .json extension for checking
    name_without_ext = filename.replace(".json", "")

    # Check if it starts with 'http' and ends with common domain patterns
    return (
        name_without_ext.startswith("http")
        and re.search(r"_(com|org|net|edu|info|biz|co_uk|blogspot_com)$", name_without_ext)
    )

def process_jsons_to_csv() -> int:
    """Process all JSON files in jsons/ directory and create a single CSV file"""

    if not JSONS_DIR.exists():
        print(f"Error: {JSONS_DIR} directory not found")
        return 1

    PROCESSED_JSONS_DIR.mkdir(parents=True, exist_ok=True)

    # Get all JSON files
    json_files = [f for f in JSONS_DIR.iterdir() if f.suffix == ".json"]

    if not json_files:
        print("No JSON files found in jsons directory")
        return 0

    print(f"Found {len(json_files)} JSON files to process")

    # Open CSV file for writing
    with AUTHORS_CONTACTS_CSV.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)

        if csvfile.tell() == 0:
            writer.writerow(['emails', 'contact_links'])

        processed_count = 0

        for json_file in json_files:
            json_path = JSONS_DIR / json_file.name

            try:
                # Read JSON file
                with json_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                # Extract data
                emails = data.get('emails', [])
                contact_links = data.get('contact_links', [])

                # Combines multiples as plain strings
                emails_str = "; ".join(emails)
                contact_links_str = "; ".join(contact_links)
                # Write row to CSV
                writer.writerow([emails_str, contact_links_str])

                # Move processed file to processed_jsons directory
                processed_path = PROCESSED_JSONS_DIR / json_file.name
                shutil.move(str(json_path), processed_path)

                processed_count += 1
                print(f"Processed: {json_file}")

            except json.JSONDecodeError as e:
                print(f"Error parsing {json_file}: {e}")
            except Exception as e:
                print(f"Error processing {json_file}: {e}")

    print(f"\nProcessing complete!")
    print(f"Successfully processed {processed_count} files")
    print(f"CSV file created: {AUTHORS_CONTACTS_CSV}")
    print(f"Processed files moved to: {PROCESSED_JSONS_DIR}/")
    return 0


def main() -> int:
    return process_jsons_to_csv()


if __name__ == "__main__":
    raise SystemExit(main())
