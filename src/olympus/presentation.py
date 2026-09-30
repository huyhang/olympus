"""Safe, human-friendly formatting shared by Olympus views."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_CSI = r"(?:\x1b\[|\x9b)[0-?]*[ -/]*"
_STRING = r"(?:\x1b[]PX^_]|[\x90\x98\x9d-\x9f])[^\x07\x1b\x9c]*"
_OTHER = r"\x1b[ -/]*"
ESCAPE_SEQUENCES = re.compile(
    rf"{_CSI}[@-~]|{_STRING}(?:\x07|\x1b\\|\x9c)|{_OTHER}[0-~]"
)
UNFINISHED_SEQUENCE = re.compile(rf"(?:{_CSI}|{_STRING}\x1b?|{_OTHER})\Z")


def plain_text(value: str) -> str:
    return CONTROL_CHARACTERS.sub("", ESCAPE_SEQUENCES.sub("", value))


def streamable(partial: str) -> str:
    tail = UNFINISHED_SEQUENCE.search(partial)
    return plain_text(partial[: tail.start()] if tail else partial)


def format_timestamp(value: str, timezone: tzinfo | None = None) -> str:
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
