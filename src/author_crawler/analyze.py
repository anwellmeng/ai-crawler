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

import asyncio, json, logging, os, re
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from config import (
    LLM_CONCURRENCY,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TOKEN_LIMIT,
)
from db import get_conn

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Extract contact info from author-website Markdown. Return ONLY this JSON, no prose:
{"emails": [...], "contact_links": [...]}

EMAILS — all professional contacts for reaching the author:
- Include: author's own email, literary agent, publicist, booking contacts
- Exclude: newsletter signups, social DMs, generic support, obvious decoys (e.g. example@example.com)
- Normalize obfuscated addresses ("name [at] domain [dot] com" -> "name@domain.com"); lowercase; deduplicate

CONTACT FORMS — URLs of pages containing a contact/message form:
- On-site forms preferred; Typeform/Google Forms acceptable if the author uses them
- Do not include mailto: links
- Resolve relative paths against any base URL present in the Markdown

Use empty arrays if nothing is found.
"""

# Markdown shorter than this is assumed to be bot-blocked or blank.
_MIN_USEFUL_TOKENS = 20

# Transient API errors worth retrying with exponential backoff.
_RETRYABLE = (RateLimitError, APITimeoutError, InternalServerError, APIConnectionError)
_MAX_RETRIES = 4          # total attempts = _MAX_RETRIES (1 initial + 3 retries)
_REQUEST_TIMEOUT = 60.0   # seconds per API call

# Matches the first {...} block (greedy), tolerating ```json fences / prose.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

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


def _as_str_list(value) -> list[str]:
    """Coerce a model field into a clean list of strings.

    Tolerates the model returning a bare string instead of a list, or a list
    with non-string elements — without this, a string value would be char-joined
    into garbage by '; '.join() downstream.
    """
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def _parse_response(content: str | None) -> tuple[list[str], list[str]]:
    """Parse the model's reply into (emails, contact_links).

    Raises ValueError on empty content and json.JSONDecodeError on unparseable
    content (both caught by the caller and recorded as a failure).
    """
    if not content or not content.strip():
        raise ValueError("empty model response")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Fall back to extracting the first {...} block (handles ```json fences).
        match = _JSON_RE.search(content)
        if not match:
            raise
        data = json.loads(match.group(0))
    return _as_str_list(data.get("emails")), _as_str_list(data.get("contact_links"))


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
            completion = await _create_with_retry(client, url, markdown)
            result = completion.choices[0].message.content
            emails, contact_links = _parse_response(result)
            return url, "done", emails, contact_links, None
        except (json.JSONDecodeError, ValueError) as exc:
            return url, "failed", [], [], f"invalid response: {exc}"
        except Exception as exc:
            return url, "failed", [], [], str(exc)


async def _create_with_retry(client: AsyncOpenAI, url: str, markdown: str):
    """Call the chat completions endpoint, retrying transient errors with
    exponential backoff. Non-transient errors propagate immediately."""
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.chat.completions.create(
                model=LLM_MODEL,
                max_tokens=LLM_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": markdown},
                ],
                response_format={"type": "json_object"},
                extra_body={"reasoning": {"effort": "low"}},
            )
        except _RETRYABLE as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = 2 ** attempt
            logger.warning(
                "Transient API error for %s (attempt %d/%d): %s — retrying in %ds",
                url, attempt + 1, _MAX_RETRIES, exc, delay,
            )
            await asyncio.sleep(delay)


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
        timeout=_REQUEST_TIMEOUT,
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
