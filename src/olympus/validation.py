"""Input checks shared by Olympus and the agents it hosts."""

from __future__ import annotations

import httpx


def http_url_problem(value: str, label: str) -> str | None:
    """Why `value` cannot be used as an HTTP endpoint, or None when it can.

    Parsing with httpx catches what a prefix check misses, such as a letter
    in the port, so a typo is refused when saved rather than failing later
    inside a reply.
    """
    try:
        url = httpx.URL(value.strip())
    except (httpx.InvalidURL, TypeError):
        return f"{label} is not a valid URL."
    if url.scheme not in {"http", "https"} or not url.host:
        return f"{label} must begin with http:// or https:// and name a host."
    return None
