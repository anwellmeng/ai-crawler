"""
Analyze stage.

Reads crawl_status='crawled' AND analyze_status='pending' rows from the DB,
sends each author's markdown to the LLM to extract emails and contact links,
and writes results back to the DB.

Markdown exceeding LLM_TOKEN_LIMIT is truncated from the bottom before
sending — contact info is almost always near the top of a site.
Empty or near-empty markdown is marked 'skipped' (bot protection, blank page,
crawl that returned no useful content, etc.).

Status transitions:
  pending → done     (successful extraction)
  pending → failed   (API error or invalid JSON response)
  pending → skipped  (empty / too-short markdown)
"""
from __future__ import annotations

import asyncio, json, logging, os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from config import (
    LLM_CONCURRENCY,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TOKEN_LIMIT,
)
from db import get_conn

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract contact info from scraped author-website Markdown.

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
- No extra prose in your response.

EMAILS
- Accept from visible text and mailto:.
- Normalize: lowercase; replace [at]/(at)/" at " -> "@"; [dot]/(dot)/" dot " -> "."; remove spaces/zero-width.
- Validate simple pattern: local@domain.tld, tld 2-24 letters.
- Discard obvious decoys like example@example.com.

CONTACT FORMS
- Include pages that host a contact form or clearly instruct submitting a message.
- Prefer on-site forms; if none, include reputable off-site forms used by the author (Typeform, Google Forms).
- Do NOT count mailto: as a contact form.
- If a <form> action is shown, include the PAGE URL containing it.
- If a base URL is present in the Markdown (e.g., "Source: https://site.com/page"), resolve relative paths against it; otherwise return the relative path.

END: Output exactly the JSON object per schema above.
"""

# Markdown shorter than this is assumed to be bot-blocked or blank.
_MIN_USEFUL_TOKENS = 20

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


def _token_count(text: str) -> int:
    if _ENC:
        return len(_ENC.encode(text))
    return len(text) // 4


def _truncate(text: str, limit: int) -> str:
    if _ENC is None:
        return text[: limit * 4]
    tokens = _ENC.encode(text)
    if len(tokens) <= limit:
        return text
    return _ENC.decode(tokens[:limit])


# ── DB writes ─────────────────────────────────────────────────────────────────

def _mark_done(url: str, emails: list[str], contact_links: list[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE authors
               SET analyze_status = 'done',
                   emails         = ?,
                   contact_links  = ?,
                   analyze_error  = NULL,
                   updated_at     = datetime('now')
               WHERE url = ?""",
            ("; ".join(emails), "; ".join(contact_links), url),
        )


def _mark_failed(url: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE authors
               SET analyze_status = 'failed',
                   analyze_error  = ?,
                   updated_at     = datetime('now')
               WHERE url = ?""",
            (error, url),
        )


def _mark_skipped(url: str, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE authors
               SET analyze_status = 'skipped',
                   analyze_error  = ?,
                   updated_at     = datetime('now')
               WHERE url = ?""",
            (reason, url),
        )


# ── Per-author analysis ───────────────────────────────────────────────────────

async def _analyze_one(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    url: str,
    markdown: str,
) -> tuple[str, str, list[str], list[str], str | None]:
    """Returns (url, status, emails, contact_links, error_msg)."""
    async with semaphore:
        tok = _token_count(markdown)

        if tok < _MIN_USEFUL_TOKENS:
            return url, "skipped", [], [], "too short — likely bot protection or blank page"

        if tok > LLM_TOKEN_LIMIT:
            markdown = _truncate(markdown, LLM_TOKEN_LIMIT)
            logger.info("Truncated %s (%d → %d tokens)", url, tok, LLM_TOKEN_LIMIT)

        try:
            completion = await client.chat.completions.create(
                model=LLM_MODEL,
                max_tokens=LLM_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": markdown},
                ],
            )
            result = completion.choices[0].message.content
            data = json.loads(result)
            return url, "done", data.get("emails", []), data.get("contact_links", []), None
        except json.JSONDecodeError as exc:
            return url, "failed", [], [], f"invalid JSON: {exc}"
        except Exception as exc:
            return url, "failed", [], [], str(exc)


# ── Stage entry point ─────────────────────────────────────────────────────────

async def analyze() -> int:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT url, markdown FROM authors
               WHERE crawl_status = 'crawled' AND analyze_status = 'pending'"""
        ).fetchall()

    if not rows:
        print("No rows ready to analyze.")
        return 0

    print(f"Analyzing {len(rows)} author(s) (concurrency={LLM_CONCURRENCY})...")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY is not set.")
        return 1

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    done = failed = skipped = 0

    tasks = [
        asyncio.create_task(
            _analyze_one(client, semaphore, row["url"], row["markdown"] or "")
        )
        for row in rows
    ]

    for coro in asyncio.as_completed(tasks):
        url, status, emails, contact_links, error = await coro
        if status == "done":
            _mark_done(url, emails, contact_links)
            done += 1
        elif status == "skipped":
            _mark_skipped(url, error)
            skipped += 1
        else:
            _mark_failed(url, error)
            failed += 1

        completed = done + failed + skipped
        if completed % 100 == 0:
            print(f"  {completed}/{len(rows)} complete …")

    print(f"Analyze complete: {done} done, {failed} failed, {skipped} skipped.")
    return 0
