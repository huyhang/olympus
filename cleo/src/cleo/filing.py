"""Filing a folder of volumes: Cleo's side of an Olympus review board.

Each volume moves through a small state machine. Cleo discovers it, matches
it to a series, and stages it, so Nineveh itself names the destination and
any duplicate before the user decides. Only the user's approval commits a
volume to the library. Anything staged and left undecided is withdrawn,
including an upload still in flight when the review ends.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from cleo.domain import LocalVolume, SeriesOption
from cleo.ingest import MAX_FILENAME
from cleo.ports import CatalogError, Inbox, IngestError, IngestGateway, SeriesFinder
from olympus.domain import Candidate, Evidence, Proposal
from olympus.ports import ProposalListener, ReviewError
from olympus.presentation import human_size, plain_text

TITLE = "File volumes from a folder"
UPLOAD_SLOTS = 2
PROGRESS_STEP = 5
# How long quitting Olympus waits for Nineveh to answer uploads it already
# holds in full, so they can be withdrawn rather than left in its queue.
SHUTDOWN_GRACE = 5.0
CHECKING = "Uploaded; Nineveh is checking it…"
HELD = (
    "Uploaded. Placing it needs a token with ingest:commit; it waits in "
    "Nineveh's approval queue."
)
REFUSALS = {
    "place": "That volume is not ready to place.",
    "skip": "That volume is still being prepared.",
    "choose": "That volume is still being prepared.",
    "rename": "Only a volume that is ready to place can be renamed.",
}
ALLOWED = {
    "place": frozenset({"ready"}),
    "skip": frozenset({"pending", "needs_choice", "ready", "failed"}),
    "choose": frozenset({"needs_choice", "ready", "failed"}),
    "rename": frozenset({"ready"}),
}
KEEP_UPLOAD = frozenset({"placed", "held"})


@dataclass(frozen=True, slots=True)
class FilingServices:
    inbox: Inbox
    finder: SeriesFinder
    ingest: IngestGateway


class FilingWorkflow:
    """The `olympus.ports.FolderWorkflow` a filing-enabled Cleo offers.

    It outlives each review, so it keeps every session that may still hold
    uploads, and withdraws them in `aclose` when Olympus quits.
    """

    def __init__(self, services: FilingServices, slots: int = UPLOAD_SLOTS) -> None:
        self._services = services
        self._slots = slots
        self._sessions: set[FilingSession] = set()

    @property
    def title(self) -> str:
        return TITLE

    def describe(self, folder: Path, recursive: bool) -> str:
        scan = self._services.inbox.scan(folder, recursive)
        if not scan.volumes:
            hint = "" if recursive else " Try including subfolders."
            return f"No .cbz volumes here.{hint}"
        count = len(scan.volumes)
        size = human_size(sum(item.size for item in scan.volumes))
        line = f"{count} .cbz volume{'' if count == 1 else 's'} · {size}"
        return f"{line} · {scan.limit}" if scan.limit else line

    def open(
        self, folder: Path, recursive: bool, listener: ProposalListener
    ) -> FilingSession:
        session = FilingSession(
            folder,
            recursive,
            listener,
            self._services,
            asyncio.Semaphore(self._slots),
            release=self._sessions.discard,
        )
        self._sessions.add(session)
        return session

    async def aclose(self) -> None:
        """Olympus is quitting: withdraw what every unfinished review left."""
        await asyncio.gather(*(session.shutdown() for session in list(self._sessions)))


@dataclass
class _Item:
    volume: LocalVolume
    proposal: Proposal
    ingest_id: str = ""
    staged: dict[str, Any] = field(default_factory=dict)
    shown_percent: int = -1
    # The share of the file handed to Nineveh so far. Below 1.0, Nineveh
    # cannot have staged anything, so stopping the upload leaves nothing.
    sent: float = 0.0
    upload: asyncio.Task[None] | None = None


class FilingSession:
    """One folder under review. Implements `olympus.ports.ReviewSession`."""

    def __init__(
        self,
        folder: Path,
        recursive: bool,
        listener: ProposalListener,
        services: FilingServices,
        slots: asyncio.Semaphore,
        release: Callable[[FilingSession], None] = lambda session: None,
    ) -> None:
        self._folder = folder
        self._recursive = recursive
        self._listener = listener
        self._services = services
        self._slots = slots
        self._release = release
        self._items: dict[str, _Item] = {}
        self._options: dict[str, SeriesOption] = {}
        self._uploads: set[asyncio.Task[None]] = set()
        self._closed = False

    # -- discovery and preparation ---------------------------------------------

    async def run(self) -> str:
        try:
            scan = await asyncio.to_thread(
                self._services.inbox.scan, self._folder, self._recursive
            )
        except OSError as error:
            raise ReviewError(
                f"Cannot read {self._folder}: {error.strerror or error}"
            ) from error
        items = [self._add(volume) for volume in scan.volumes]
        await asyncio.gather(*(self._prepare(item) for item in items))
        return scan.limit

    def _add(self, volume: LocalVolume) -> _Item:
        proposal = Proposal(
            id=volume.relative, source=volume.relative, size=volume.size
        )
        if volume.problem:
            proposal = replace(
                proposal, state="failed", activity=volume.problem, retryable=False
            )
        item = self._items[volume.relative] = _Item(volume, proposal)
        self._emit(item)
        return item

    async def _prepare(self, item: _Item) -> None:
        async with self._slots:
            if item.proposal.state != "pending" or self._closed:
                return
            self._update(item, state="working", activity="Matching…")
            try:
                match = await self._services.finder.match(item.volume)
            except CatalogError as error:
                self._update(item, state="failed", activity=str(error))
                return
            self._remember(match.alternatives)
            alternatives = tuple(option.candidate for option in match.alternatives)
            if match.series is None:
                self._update(
                    item,
                    state="needs_choice",
                    activity=match.reason,
                    alternatives=alternatives,
                )
                return
            self._remember((match.series,))
            await self._stage(item, match.series, match.reason, alternatives)

    async def _stage(
        self,
        item: _Item,
        option: SeriesOption,
        note: str,
        alternatives: tuple[Candidate, ...],
    ) -> None:
        """Upload under `option`; Nineveh answers with where it would land."""
        if self._closed:
            return
        self._update(
            item,
            state="working",
            activity="Uploading…",
            subject=option.subject,
            subject_note=note,
            alternatives=alternatives,
            **CLEARED,
        )
        upload = item.upload = asyncio.create_task(self._upload(item, option))
        self._uploads.add(upload)
        upload.add_done_callback(self._upload_finished)
        # Shielded, so leaving the board cancels only this wait. `close` then
        # decides the upload's fate: stopped, or withdrawn once it lands.
        await asyncio.shield(upload)

    async def _upload(self, item: _Item, option: SeriesOption) -> None:
        item.shown_percent, item.sent = -1, 0.0
        try:
            staged = await self._send(item, option)
        except IngestError as error:
            self._update(item, state="failed", activity=str(error))
            return
        ingest_id = str(staged.get("ingestId") or "")
        if self._closed:
            # The review ended while Nineveh checked the file.
            await self._discard(ingest_id)
            return
        item.ingest_id = ingest_id
        item.staged = staged
        self._update(item, **staged_changes(staged))

    async def _send(self, item: _Item, option: SeriesOption) -> dict[str, Any]:
        try:
            stream = self._services.inbox.open(item.volume)
        except OSError as error:
            raise IngestError(
                f"Cannot read the file: {error.strerror or error}"
            ) from error
        with stream:
            return await self._services.ingest.stage(
                option.series_id,
                item.volume.path.name,
                stream,
                lambda fraction: self._progress(item, fraction),
            )

    def _progress(self, item: _Item, fraction: float) -> None:
        item.sent = fraction
        percent = int(fraction * 100) // PROGRESS_STEP * PROGRESS_STEP
        if item.proposal.state == "working" and percent != item.shown_percent:
            item.shown_percent = percent
            activity = CHECKING if percent >= 100 else f"Uploading {percent}%"
            self._update(item, activity=activity)

    def _upload_finished(self, upload: asyncio.Task[None]) -> None:
        self._uploads.discard(upload)
        self._release_when_idle()

    # -- the user's decisions --------------------------------------------------

    async def place(self, proposal_id: str) -> None:
        item = self._find(proposal_id, "place")
        proposal = item.proposal
        self._update(item, state="placing", activity="Placing…")
        renamed = proposal.filename != proposal.suggested_filename
        try:
            placed = await self._services.ingest.commit(
                item.ingest_id, proposal.filename if renamed else None
            )
        except IngestError as error:
            if error.status == 403:
                self._update(
                    item, state="held", activity=HELD, evidence=self._evidence(item)
                )
            else:
                self._update(item, state="ready", activity=f"Not placed: {error}")
            return
        item.ingest_id = ""
        self._update(item, **placed_changes(placed, proposal))

    async def skip(self, proposal_id: str) -> None:
        item = self._find(proposal_id, "skip")
        withdrawn = await self._withdraw(item)
        note = "" if withdrawn else "Skipped, but Nineveh still holds its upload."
        self._update(item, state="skipped", activity=note)

    async def rename(self, proposal_id: str, filename: str) -> None:
        item = self._find(proposal_id, "rename")
        name = checked_filename(filename, item.proposal.suggested_filename)
        self._update(item, filename=name, activity="")

    async def choose(self, proposal_id: str, choice: Candidate) -> None:
        item = self._find(proposal_id, "choose")
        option = self._options.get(choice.id) or SeriesOption(choice.id, choice.title)
        alternatives = item.proposal.alternatives
        self._update(item, state="working", activity="Waiting to upload…")
        await self._withdraw(item)
        note = " · ".join(filter(None, (option.library, "chosen by you")))
        async with self._slots:
            await self._stage(item, option, note, alternatives)

    async def search(self, query: str) -> tuple[Candidate, ...]:
        query = query.strip()
        if not query:
            return ()
        try:
            options = await self._services.finder.search(query)
        except CatalogError as error:
            raise ReviewError(str(error)) from error
        self._remember(options)
        return tuple(option.candidate for option in options)

    async def close(self) -> None:
        """Withdraw what was prepared and not settled, in flight or not.

        An upload Nineveh has not received in full is stopped, so nothing is
        staged. One it already holds withdraws itself when Nineveh answers.
        """
        if self._closed:
            return
        self._closed = True
        stopped = []
        for item in self._items.values():
            if item.upload is not None and not item.upload.done():
                if item.sent < 1.0:
                    item.upload.cancel()
                    stopped.append(item.upload)
            elif item.proposal.state not in KEEP_UPLOAD:
                await self._withdraw(item)
        await asyncio.gather(*stopped, return_exceptions=True)
        self._release_when_idle()

    async def shutdown(self) -> None:
        """Olympus is quitting: close, then wait briefly for uploads to land."""
        await self.close()
        if self._uploads:
            await asyncio.wait(set(self._uploads), timeout=SHUTDOWN_GRACE)
        for upload in set(self._uploads):
            upload.cancel()
        await asyncio.gather(*self._uploads, return_exceptions=True)

    # -- bookkeeping -------------------------------------------------------------

    def _find(self, proposal_id: str, action: str) -> _Item:
        item = self._items.get(proposal_id)
        if item is None:
            raise ReviewError("That volume is no longer part of this review.")
        if item.proposal.state not in ALLOWED[action]:
            raise ReviewError(REFUSALS[action])
        if action == "choose" and not item.proposal.retryable:
            raise ReviewError(f"{item.volume.problem} It cannot be uploaded.")
        return item

    async def _withdraw(self, item: _Item) -> bool:
        """Discard the item's staged upload, if any. False if Nineveh kept it."""
        if not await self._discard(item.ingest_id):
            return False
        item.ingest_id = ""
        item.staged = {}
        return True

    async def _discard(self, ingest_id: str) -> bool:
        if not ingest_id:
            return True
        try:
            await self._services.ingest.discard(ingest_id)
        except IngestError:
            return False
        return True

    def _release_when_idle(self) -> None:
        if self._closed and not self._uploads:
            self._release(self)

    def _evidence(self, item: _Item) -> tuple[Evidence, ...]:
        arguments = {"source": item.proposal.source}
        return (Evidence("stage_volume", item.staged, arguments=arguments),)

    def _remember(self, options: Iterable[SeriesOption]) -> None:
        for option in options:
            self._options[option.series_id] = option

    def _update(self, item: _Item, **changes: Any) -> None:
        item.proposal = replace(item.proposal, **changes)
        self._emit(item)

    def _emit(self, item: _Item) -> None:
        if not self._closed:
            self._listener(item.proposal)


# What a new upload must not inherit from an earlier one.
CLEARED: dict[str, Any] = {
    "destination": "",
    "filename": "",
    "suggested_filename": "",
    "pattern": "",
    "warning": "",
    "pages": 0,
}


def staged_changes(staged: dict[str, Any]) -> dict[str, Any]:
    """A proposal's fields from Nineveh's account of a staged upload."""
    target = plain_text(str(staged.get("targetPath") or ""))
    suggested = plain_text(str(staged.get("suggestedFilename") or ""))
    suggested = suggested or posixpath.basename(target)
    folder = posixpath.dirname(target) if target.endswith(suggested) else target
    duplicate = staged.get("duplicateOf")
    warning = ""
    if isinstance(duplicate, dict) and duplicate.get("filename"):
        warning = f"Already in the library as {plain_text(str(duplicate['filename']))}"
    return {
        "state": "ready",
        "activity": "",
        "destination": folder,
        "filename": suggested,
        "suggested_filename": suggested,
        "pattern": plain_text(str(staged.get("siblingPattern") or "")),
        "warning": warning,
        "pages": _count(staged.get("pageCount")),
    }


def placed_changes(placed: dict[str, Any], proposal: Proposal) -> dict[str, Any]:
    """A proposal's fields once Nineveh reports the volume placed."""
    path = plain_text(str(placed.get("relativePath") or ""))
    filename = posixpath.basename(path) or proposal.filename
    folder = posixpath.dirname(path) if path else proposal.destination
    arguments = {"source": proposal.source, "series": proposal.subject_note}
    return {
        "state": "placed",
        "activity": "",
        "destination": folder,
        "filename": filename,
        "evidence": (Evidence("place_volume", placed, arguments=arguments),),
    }


def checked_filename(filename: str, suggested: str) -> str:
    """A user's rename, held to what a filename in one library folder can be."""
    name = plain_text(filename).strip()
    if not name or name in {".", ".."}:
        raise ReviewError("Enter a filename.")
    if "/" in name or "\\" in name:
        raise ReviewError("A filename cannot contain folders.")
    if len(name) > MAX_FILENAME:
        raise ReviewError(f"Filenames may be at most {MAX_FILENAME} characters.")
    extension = posixpath.splitext(suggested)[1]
    if extension and not name.casefold().endswith(extension.casefold()):
        raise ReviewError(f"Keep the {extension} extension.")
    return name


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
