"""What a volume's own name and metadata say about its series.

Pure functions: the files themselves are read by `cleo.inbox`.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

from cleo.domain import VolumeHint
from olympus.presentation import plain_text

MAX_TITLE_LENGTH = 200
EXTENSION = re.compile(r"\.cbz$", re.IGNORECASE)
# Release-group tags, years, and quality notes: `[Group]`, `(2024)`, `{HQ}`.
BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
SEPARATORS = re.compile(r"[_.]+")
SPACES = re.compile(r"\s+")
VOLUME_MARKER = re.compile(
    r"(?:^|(?<=[\s-]))(?:v|vol|volume|tome|book|#)\s*\.?\s*(\d{1,4}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
TRAILING_NUMBER = re.compile(r"\s(\d{1,4}(?:\.\d+)?)$")
TITLE_EDGES = " -–—,:;"


def hint_from_filename(name: str) -> VolumeHint | None:
    """`[Group] Vinland.Saga.v12 (Digital).cbz` → Vinland Saga, volume 12."""
    stem = _readable(EXTENSION.sub("", name))
    marker = VOLUME_MARKER.search(stem) or TRAILING_NUMBER.search(stem)
    title, number = (
        (stem[: marker.start()], marker.group(1)) if marker else (stem, None)
    )
    title = title.strip(TITLE_EDGES)
    if not title or len(title) > MAX_TITLE_LENGTH:
        return None
    return VolumeHint(title, _number(number), "filename")


def hint_from_comicinfo(document: str) -> VolumeHint | None:
    """The series a ComicInfo.xml names, which beats any filename."""
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return None
    title = SPACES.sub(" ", plain_text(root.findtext("Series") or "")).strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        return None
    number = (root.findtext("Number") or root.findtext("Volume") or "").strip()
    return VolumeHint(title, _number(number or None), "comicinfo")


def _readable(stem: str) -> str:
    stem = BRACKETED.sub(" ", plain_text(stem))
    stem = SPACES.sub(" ", SEPARATORS.sub(" ", stem)).strip()
    # `vinland-saga-v09` uses hyphens as spaces; `Spider-Man v01` does not.
    return stem if " " in stem else stem.replace("-", " ")


def _number(value: str | None) -> str | None:
    """`012` → `12`, `7.5` stays; anything that is not a number is dropped."""
    if not value:
        return None
    whole, _, fraction = value.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    return f"{int(whole)}.{fraction}" if fraction else str(int(whole))
