from openai import OpenAI
from pathlib import Path
import os 

import sys # TERMINAL LOGGING 
sys.stdout = open("run_log.txt", "a", encoding="utf-8")
sys.stderr = sys.stdout
print("\n--- New run started ---")

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key="[REDACTED_OPENROUTER]",
)

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base") # Roo Code suggested
except Exception:
    _ENC = None

def count_tokens(text: str) -> int:
    if _ENC:
        return len(_ENC.encode(text))
    # fallback approximation if tiktoken unavailable
    return int(len(text) / 4)

def rename_with_skipped(p: Path) -> Path:
    target = p.with_name(f"{p.stem}_SKIPPED{p.suffix}")
    if target.exists():
        i = 1
        while True:
            cand = p.with_name(f"{p.stem}_SKIPPED_{i}{p.suffix}")
            if not cand.exists():
                target = cand
                break
            i += 1
    p.rename(target)
    return target

for file in Path("to_analyze").glob("*.md"):
  with file.open(encoding="utf-8") as f:
    md = f.read()

  LIMIT = 159_000
  tok = count_tokens(md)
  if tok > LIMIT:
      # Create the skipped_sites directory if it doesn't exist
      skipped_dir = Path("skipped_sites")
      skipped_dir.mkdir(exist_ok=True)
      # Generate a unique name for the skipped file
      base = file.stem
      ext = file.suffix
      num = 1
      while True:
          new_name = f"{base}_SKIPPED_{num}{ext}"
          new_path = skipped_dir / new_name
          if not new_path.exists():
              break
          num += 1
      # Move the file to the new path
      file.rename(new_path)
      print(f"Skipped (tokens={tok}): {new_path}")
      continue
  
  completion = client.chat.completions.create(
    model="deepseek/deepseek-chat-v3.1:free",
    messages=[
      {
        "role": "system",
        "content": """You extract contact info from scraped author-website Markdown.

INPUT: One Markdown string (may contain multiple pages). Links may be absolute or relative. Emails may be obfuscated (e.g., "name [at] domain [dot] com", "name(at)domain(dot)com", "name at domain dot com"), include spaces, or zero-width chars.

TASK: Find
1) author email addresses
2) links to a contact form

OUTPUT: Return ONLY a single JSON object (no code fences, no prose):
{"emails":[...],"contact_links":[...]}

RULES
- Always include both keys; if none, use empty arrays.
- Do not guess or invent data.
- Deduplicate. Priority order: author > agent/publicist > publisher/booking.
- Exclude: newsletter signups, press kits, social DMs, RSS, generic support portals.

EMAILS
- Accept from visible text and mailto:.
- Normalize: lowercase; replace [at]/(at)/“ at ” → "@"; [dot]/(dot)/“ dot ” → "."; remove spaces/zero-width.
- Validate simple pattern: local@domain.tld, tld 2–24 letters.
- Discard obvious decoys like example@example.com.

CONTACT FORMS
- Include pages that host a contact form or clearly instruct submitting a message.
- Prefer on-site forms; if none, include reputable off-site forms used by the author (Typeform, Google Forms).
- Do NOT count mailto: as a contact form.
- If a <form> action is shown, include the PAGE URL containing it.
- If a base URL is present in the Markdown (e.g., "Source: https://site.com/page"), resolve relative paths against it; otherwise return the relative path.

END: Output exactly the JSON object per schema above.
  """
      },
      {"role": "user", "content": md}
    ]
  )
  # Save to .json
  output_dir = Path("jsons")
  output_dir.mkdir(exist_ok=True)
  json_name = os.path.splitext(file.name)[0] + ".json"
  output_path = output_dir / json_name

  result = completion.choices[0].message.content
with open(output_path, "w", encoding="utf-8") as out_file:
    out_file.write(result)

# Create the finished_sites directory if it doesn't exist
finished_dir = Path("finished_sites")
finished_dir.mkdir(exist_ok=True)

# Move the .md file to finished_sites/ directory
# Check if the file already exists in finished_sites
if file.name in [f.name for f in finished_dir.iterdir()]:
    # If it exists, find a unique name
    base = file.stem
    ext = file.suffix
    num = 1
    while True:
        new_name = f"{base}_{num}{ext}"
        new_path = finished_dir / new_name
        if not new_path.exists():
            break
        num += 1
    file.rename(finished_dir / new_name)
    print(f"Moved to finished_sites: {finished_dir / new_name}")
else:
    file.rename(finished_dir / file.name)
    print(f"Moved to finished_sites: {finished_dir / file.name}")



