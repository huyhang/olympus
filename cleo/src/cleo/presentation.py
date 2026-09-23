"""Human-friendly formatting shared by the terminal views."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain_text(value: str) -> str:
    """Strip terminal control sequences from untrusted text.

    Series titles and filenames come off downloaded volumes, and model output
    is only as trustworthy as what it read. An escape sequence in either would
    be executed by the terminal rather than displayed. Tabs and newlines stay.
    """
    return CONTROL_CHARACTERS.sub("", value)


def format_timestamp(value: str, timezone: tzinfo | None = None) -> str:
    """Render a stored ISO timestamp in the requested or local timezone."""
    try:
        moment = datetime.fromisoformat(value).astimezone(timezone)
    except ValueError:
        return value
    hour = moment.strftime("%I").lstrip("0") or "0"
    zone = moment.tzname()
    suffix = f" {zone}" if zone else ""
    return (
        f"{moment.strftime('%b')} {moment.day}, {moment.year} at "
        f"{hour}:{moment.strftime('%M %p')}{suffix}"
    )
