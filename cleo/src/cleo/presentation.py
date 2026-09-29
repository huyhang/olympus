"""Human-friendly formatting shared by the terminal views."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Whole sequences, each in its 7-bit (ESC ...) and 8-bit (C1) spelling.
# Removing only the introducer would leave the rest -- `[31m` -- on screen.
_CSI = r"(?:\x1b\[|\x9b)[0-?]*[ -/]*"  # colour, cursor movement, erasing
_STRING = r"(?:\x1b[]PX^_]|[\x90\x98\x9d-\x9f])[^\x07\x1b\x9c]*"  # titles, links
_OTHER = r"\x1b[ -/]*"  # ESC c, ESC 7, ESC ( B and the rest
ESCAPE_SEQUENCES = re.compile(
    rf"{_CSI}[@-~]|{_STRING}(?:\x07|\x1b\\|\x9c)|{_OTHER}[0-~]"
)
# A sequence at the very end of a stream that may not have finished arriving.
UNFINISHED_SEQUENCE = re.compile(rf"(?:{_CSI}|{_STRING}\x1b?|{_OTHER})\Z")


def plain_text(value: str) -> str:
    """Strip terminal control sequences from untrusted text.

    Series titles and filenames come off downloaded volumes, and model output
    is only as trustworthy as what it read. An escape sequence in either would
    be executed by the terminal rather than displayed. A well-formed sequence
    goes entirely; a malformed one loses its introducer, leaving plain text the
    terminal cannot act on. Tabs and newlines stay.
    """
    return CONTROL_CHARACTERS.sub("", ESCAPE_SEQUENCES.sub("", value))


def streamable(partial: str) -> str:
    """What of a still-arriving text can be shown now.

    A model streams a token at a time, so a sequence can arrive in pieces;
    cleaning each piece alone would show `[31m` until the next one landed.
    Holding back an unfinished tail means the text shown so far only ever
    grows, and never contains what the finished text will not.
    """
    tail = UNFINISHED_SEQUENCE.search(partial)
    return plain_text(partial[: tail.start()] if tail else partial)


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
