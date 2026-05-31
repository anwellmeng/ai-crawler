"""Shared utilities used across pipeline stages."""

from __future__ import annotations

from urllib.parse import urlparse

from config import BLOCKED_DOMAINS


def is_blocked_url(url: str) -> bool:
    """Return True if url's domain is in BLOCKED_DOMAINS (subdomains included)."""
    netloc = urlparse(url).netloc.lower().removeprefix("www.")
    return any(netloc == d or netloc.endswith("." + d) for d in BLOCKED_DOMAINS)
