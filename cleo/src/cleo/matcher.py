"""Which catalog series a volume belongs to.

The cheapest reliable evidence goes first: the archive's ComicInfo.xml, then
its cleaned-up filename, each put to Nineveh's own title resolution. The model
is asked only when both fail, and only for a better search term; Nineveh still
decides, and when it cannot, the user does. The model may then suggest one of
Nineveh's candidates, which the user sees first and must still choose.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from cleo.domain import LocalVolume, MatchResult, SeriesOption, VolumeHint
from cleo.naming import MAX_TITLE_LENGTH, hint_from_comicinfo, hint_from_filename
from cleo.ports import (
    CandidateRanker,
    CatalogGateway,
    ChatModel,
    Inbox,
    ModelError,
    TitleGuesser,
)
from olympus.presentation import plain_text

SEARCH_LIMIT = 8
SOURCES = {
    "comicinfo": "named in its ComicInfo.xml",
    "filename": "read from the filename",
    "model": "suggested by the model",
}
UNKNOWN = "UNKNOWN"
GUESS_PROMPT = (
    "You read comic and manga filenames. Reply with only the series title the "
    "filename names, written the usual way, without volume numbers, release "
    f"groups, or any other words. Reply {UNKNOWN} if you cannot tell."
)
RANK_PROMPT = (
    "You match a comic or manga file to a catalog series. Reply with only the "
    "number of the listed series the file belongs to, or NONE if none fits."
)
NUMBER = re.compile(r"\d+")


class SeriesMatcher:
    def __init__(
        self,
        catalog: CatalogGateway,
        inbox: Inbox,
        guesser: TitleGuesser | None = None,
        ranker: CandidateRanker | None = None,
    ) -> None:
        self._catalog = catalog
        self._inbox = inbox
        self._guesser = guesser
        self._ranker = ranker

    async def match(self, volume: LocalVolume) -> MatchResult:
        """Raises CatalogError when Nineveh cannot be searched at all."""
        hints = await asyncio.to_thread(self.hints, volume)
        seen: dict[str, SeriesOption] = {}
        for hint in hints:
            found = await self._resolve(hint, seen)
            if found is not None:
                return found
        guessed = await self._guess(volume, hints)
        if guessed is not None:
            found = await self._resolve(guessed, seen)
            if found is not None:
                return found
        options = tuple(seen.values())
        suggestion = await self._suggest(volume, hints, options)
        if suggestion is not None:
            options = (suggestion, *(o for o in options if o is not suggestion))
        return MatchResult(None, options, _unresolved(hints, seen), suggestion)

    async def search(self, query: str) -> tuple[SeriesOption, ...]:
        payload = await self._catalog.search_series(
            query=query[:MAX_TITLE_LENGTH], limit=SEARCH_LIMIT
        )
        return options_of(payload)

    def hints(self, volume: LocalVolume) -> list[VolumeHint]:
        """What the volume says about itself, most trustworthy first."""
        document = self._inbox.comic_info(volume)
        found = [
            hint_from_comicinfo(document) if document else None,
            hint_from_filename(volume.path.name),
        ]
        hints: list[VolumeHint] = []
        for hint in found:
            if hint and all(hint.title.casefold() != h.title.casefold() for h in hints):
                hints.append(hint)
        return hints

    async def _resolve(
        self, hint: VolumeHint, seen: dict[str, SeriesOption]
    ) -> MatchResult | None:
        payload = await self._catalog.search_series(
            query=hint.title, limit=SEARCH_LIMIT
        )
        options = options_of(payload)
        for option in options:
            seen.setdefault(option.series_id, option)
        chosen = confident(payload, options)
        if chosen is None:
            return None
        others = tuple(item for item in options if item is not chosen)
        return MatchResult(chosen, others, describe_match(chosen, hint))

    async def _guess(
        self, volume: LocalVolume, hints: list[VolumeHint]
    ) -> VolumeHint | None:
        if self._guesser is None:
            return None
        title = await self._guesser.guess(volume.path.name)
        if not title or any(title.casefold() == h.title.casefold() for h in hints):
            return None
        return VolumeHint(title, None, "model")

    async def _suggest(
        self,
        volume: LocalVolume,
        hints: list[VolumeHint],
        options: tuple[SeriesOption, ...],
    ) -> SeriesOption | None:
        """The model's pick among what Nineveh would not decide, if it has one."""
        if self._ranker is None or not options:
            return None
        titles = tuple(hint.title for hint in hints)
        return await self._ranker.rank(volume.path.name, titles, options)


class ModelTitleGuesser:
    """Asks the local model to read a filename no rule could."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def guess(self, filename: str) -> str | None:
        reply = await ask(self._model, GUESS_PROMPT, filename)
        return clean_guess(reply) if reply is not None else None


class ModelRanker:
    """Asks the local model which of Nineveh's uncertain matches fits a file.

    Only ever a suggestion: the user still chooses, and nothing is placed on
    the model's word.
    """

    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def rank(
        self, filename: str, titles: tuple[str, ...], options: tuple[SeriesOption, ...]
    ) -> SeriesOption | None:
        reply = await ask(
            self._model, RANK_PROMPT, rank_question(filename, titles, options)
        )
        return picked(reply, options) if reply is not None else None


async def ask(model: ChatModel, system: str, question: str) -> str | None:
    """One tool-free exchange with the model; None if it fails or calls a tool."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    parts: list[str] = []
    called_tool = False
    try:
        # Read to the end, so the stream closes before this returns.
        async for chunk in model.stream_chat(messages, ()):
            called_tool = called_tool or bool(chunk.tool_calls)
            parts.append(chunk.content)
    except ModelError:
        return None
    return None if called_tool else "".join(parts)


def rank_question(
    filename: str, titles: tuple[str, ...], options: tuple[SeriesOption, ...]
) -> str:
    lines = [f"File: {filename}"]
    lines += [f"It names: {title}" for title in titles]
    lines += ["", "Series:"]
    lines += [
        f"{number}. {option.candidate.title}"
        for number, option in enumerate(options, 1)
    ]
    return "\n".join(lines)


def picked(reply: str, options: tuple[SeriesOption, ...]) -> SeriesOption | None:
    """The option a reply names by number; None for anything else."""
    found = NUMBER.search(plain_text(reply))
    if found is None:
        return None
    index = int(found.group()) - 1
    return options[index] if 0 <= index < len(options) else None


def clean_guess(reply: str) -> str | None:
    """The model's title, or None for anything that is not plainly one."""
    lines = plain_text(reply).strip().splitlines()
    title = lines[0].strip().strip("\"'`*.").strip() if lines else ""
    if not title or title.upper() == UNKNOWN or len(title) > 120:
        return None
    return title


def options_of(payload: Any) -> tuple[SeriesOption, ...]:
    """The series in Nineveh's answer to a title search."""
    items = payload.get("candidates") if isinstance(payload, dict) else None
    found: list[SeriesOption] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("seriesId"):
            continue
        title = item.get("localName") or item.get("title") or item["seriesId"]
        found.append(
            SeriesOption(
                series_id=str(item["seriesId"]),
                title=plain_text(str(title)),
                library=plain_text(str(item.get("library") or "")),
                score=_number(item.get("score")),
                matched_on=plain_text(str(item.get("matchedOn") or "")),
                volumes=_count(item.get("publicationCount")),
            )
        )
    return tuple(found)


def confident(payload: Any, options: tuple[SeriesOption, ...]) -> SeriesOption | None:
    """Nineveh's own verdict. Cleo never promotes a merely likely match."""
    wanted = payload.get("confidentMatch") if isinstance(payload, dict) else None
    return next((item for item in options if item.series_id == wanted), None)


def describe_match(option: SeriesOption, hint: VolumeHint) -> str:
    parts = [option.library] if option.library else []
    if option.score is not None:
        on = {"localName": "folder name", "alternativeTitle": "alternative title"}
        parts.append(f"{on.get(option.matched_on, 'title')} match {option.score:.2f}")
    parts.append(SOURCES[hint.source])
    return " · ".join(parts)


def _unresolved(hints: list[VolumeHint], seen: dict[str, SeriesOption]) -> str:
    if len(seen) == 1:
        [only] = seen.values()
        return f"The catalog found “{only.title}” but would not confirm it."
    if seen:
        return "Several series could match; the catalog would not pick one."
    if hints:
        return f"Nothing in the catalog matches “{hints[0].title}”."
    return "The filename does not name a series."


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
