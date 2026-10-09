from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cleo.domain import MatchResult, SeriesOption
from cleo.filing import (
    CHECKING,
    HELD,
    FilingServices,
    FilingWorkflow,
    checked_filename,
    placed_changes,
    staged_changes,
)
from cleo.ports import CatalogError, IngestError
from fakes import MemoryInbox, RecordingIngest

from cleo import filing
from olympus.domain import Candidate, Proposal
from olympus.ports import ReviewError

PLUTO = SeriesOption("pluto", "Pluto", "Manga", 1.0, "localName", 8)
SAGA = SeriesOption("saga", "Saga", "Comics", 1.0, "localName", 10)
VINLAND = SeriesOption("vinland", "Vinland Saga", "Manga", 0.3, "localName", 12)
FOLDER = Path("/downloads")


class TableFinder:
    """Matches by filename prefix; `search` finds every known series."""

    def __init__(self, table=None):
        self.table = table or {}

    async def match(self, volume):
        for prefix, result in self.table.items():
            if volume.relative.startswith(prefix):
                if isinstance(result, Exception):
                    raise result
                return result
        return MatchResult(None, (), "Nothing matches.")

    async def search(self, query):
        if query == "down":
            raise CatalogError("Nineveh is unavailable.")
        return tuple(o for o in (PLUTO, SAGA, VINLAND) if query in o.title.casefold())


FINDER = TableFinder(
    {
        "pluto": MatchResult(PLUTO, (), "Manga · read from the filename"),
        "saga": MatchResult(None, (SAGA, VINLAND), "Several series could match."),
        "broken": CatalogError("Nineveh is unavailable."),
    }
)


def run(awaitable, timeout=10):
    """Run a test's coroutine; a regression that hangs fails instead."""
    return asyncio.run(asyncio.wait_for(awaitable, timeout))


def review_for(files, ingest=None, finder=FINDER, problems=None):
    inbox = MemoryInbox(
        {name: (b"PK" + name.encode(), None) for name in files}, problems
    )
    ingest = ingest or RecordingIngest()
    seen: list[Proposal] = []
    workflow = FilingWorkflow(FilingServices(inbox, finder, ingest), slots=2)
    session = workflow.open(FOLDER, False, seen.append)
    return workflow, session, ingest, seen


def session_for(files, ingest=None, finder=FINDER, problems=None):
    _, session, ingest, seen = review_for(files, ingest, finder, problems)
    return session, ingest, seen


class GatedIngest(RecordingIngest):
    """Holds each upload once `sent` of it is on its way, until `answer` is set."""

    def __init__(self, sent):
        super().__init__()
        self.sent = sent
        self.sending = asyncio.Event()
        self.answer = asyncio.Event()
        self.stopped = []

    async def stage(self, series_id, filename, content, progress=None):
        progress(self.sent)
        self.sending.set()
        try:
            await self.answer.wait()
        except asyncio.CancelledError:
            self.stopped.append(filename)
            raise
        return await super().stage(series_id, filename, content)


async def until(predicate, rounds=200):
    for _ in range(rounds):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never held")


def discards(ingest):
    return [call for call in ingest.calls if call[0] == "discard"]


def latest(seen, proposal_id):
    return [item for item in seen if item.id == proposal_id][-1]


async def prepared(session):
    await session.run()
    return session


def test_a_matched_volume_is_staged_and_waits_for_approval():
    session, ingest, seen = session_for(["pluto_v1.cbz"])
    run(session.run())
    proposal = latest(seen, "pluto_v1.cbz")
    assert proposal.state == "ready"
    assert proposal.subject == Candidate("pluto", "Pluto")
    assert proposal.subject_note == "Manga · read from the filename"
    assert (proposal.destination, proposal.filename) == ("Manga/Pluto", "Pluto 001.cbz")
    assert proposal.pages == 3
    assert ingest.calls == [("stage", "pluto", "pluto_v1.cbz")]
    states = [item.state for item in seen]
    assert states[:3] == ["pending", "working", "working"]
    activities = [item.activity for item in seen]
    assert activities.index("Uploading 50%") < activities.index(CHECKING)


def test_unclear_and_unreadable_volumes_are_left_to_the_user():
    session, ingest, seen = session_for(
        ["saga_v11.cbz", "broken.cbz", "huge.cbz", "unreadable.cbz"],
        problems={"huge.cbz": "Larger than the 4 GB limit."},
        finder=TableFinder(
            {
                **FINDER.table,
                "unreadable": MatchResult(PLUTO, (), "read from the filename"),
            }
        ),
    )
    run(session.run())
    saga = latest(seen, "saga_v11.cbz")
    assert saga.state == "needs_choice"
    assert [item.id for item in saga.alternatives] == ["saga", "vinland"]
    assert latest(seen, "broken.cbz").activity == "Nineveh is unavailable."
    assert latest(seen, "huge.cbz").state == "failed"
    assert (
        latest(seen, "unreadable.cbz").activity
        == "Cannot read the file: Permission denied"
    )
    assert ingest.calls == []


def test_placing_commits_the_upload_and_records_evidence():
    session, ingest, seen = session_for(["pluto_v1.cbz"])

    async def exercise():
        await session.run()
        await session.place("pluto_v1.cbz")

    run(exercise())
    placed = latest(seen, "pluto_v1.cbz")
    assert placed.state == "placed"
    assert (placed.destination, placed.filename) == (
        "Manga/Placed",
        "Suggested 001.cbz",
    )
    assert placed.evidence[0].tool == "place_volume"
    assert ("commit", "up-1", None) in ingest.calls


def test_a_rename_is_sent_with_the_commit():
    session, ingest, seen = session_for(["pluto_v1.cbz"])

    async def exercise():
        await session.run()
        await session.rename("pluto_v1.cbz", "  Pluto (Deluxe) 001.cbz ")
        await session.place("pluto_v1.cbz")

    run(exercise())
    assert ("commit", "up-1", "Pluto (Deluxe) 001.cbz") in ingest.calls
    assert latest(seen, "pluto_v1.cbz").filename == "Pluto (Deluxe) 001.cbz"


def test_a_token_that_cannot_place_leaves_the_upload_awaiting_approval():
    ingest = RecordingIngest({"commit": IngestError("refused", 403)})
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        await session.run()
        await session.place("pluto_v1.cbz")
        await session.close()

    run(exercise())
    held = latest(seen, "pluto_v1.cbz")
    assert (held.state, held.activity) == ("held", HELD)
    assert held.evidence[0].tool == "stage_volume"
    assert not any(call[0] == "discard" for call in ingest.calls)


def test_any_other_placing_failure_can_be_retried():
    ingest = RecordingIngest(
        {"commit": IngestError("Nineveh reported a conflict.", 409)}
    )
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        await session.run()
        await session.place("pluto_v1.cbz")

    run(exercise())
    proposal = latest(seen, "pluto_v1.cbz")
    assert proposal.state == "ready"
    assert proposal.activity == "Not placed: Nineveh reported a conflict."


def test_a_staging_refusal_fails_only_that_volume():
    ingest = RecordingIngest({"stage": IngestError("Comics only.", 403)})
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)
    run(session.run())
    assert (
        latest(seen, "pluto_v1.cbz").state,
        latest(seen, "pluto_v1.cbz").activity,
    ) == (
        "failed",
        "Comics only.",
    )


@pytest.mark.parametrize(
    "problem", ["The file is empty.", "Larger than the 4.0 GB limit."]
)
def test_a_volume_with_a_problem_is_never_uploaded_even_when_chosen(problem):
    session, ingest, seen = session_for(["huge.cbz"], problems={"huge.cbz": problem})

    async def exercise():
        await session.run()
        await session.choose("huge.cbz", Candidate("pluto", "Pluto"))

    with pytest.raises(ReviewError, match=f"{problem} It cannot be uploaded."):
        run(exercise())
    proposal = latest(seen, "huge.cbz")
    assert (proposal.state, proposal.retryable) == ("failed", False)
    assert ingest.calls == []


def test_a_volume_nineveh_refused_can_be_tried_under_another_series():
    ingest = RecordingIngest({"stage": IngestError("Comics only.", 403)})
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        await session.run()
        assert latest(seen, "pluto_v1.cbz").retryable
        del ingest.refuse["stage"]
        await session.choose("pluto_v1.cbz", Candidate("saga", "Saga"))

    run(exercise())
    assert latest(seen, "pluto_v1.cbz").state == "ready"


def test_choosing_a_series_stages_under_it_and_replaces_an_earlier_upload():
    session, ingest, seen = session_for(["saga_v11.cbz", "pluto_v1.cbz"])

    async def exercise():
        await session.run()
        await session.choose("saga_v11.cbz", Candidate("saga", "Saga — Comics"))
        await session.choose("pluto_v1.cbz", Candidate("unknown", "Mystery"))

    run(exercise())
    saga = latest(seen, "saga_v11.cbz")
    assert (saga.state, saga.subject.title) == ("ready", "Saga")
    assert saga.subject_note == "Comics · chosen by you"
    assert ("discard", "up-1") in ingest.calls
    assert latest(seen, "pluto_v1.cbz").subject == Candidate("unknown", "Mystery")


def test_skipping_withdraws_the_upload_and_says_when_it_could_not():
    ingest = RecordingIngest()
    session, _, seen = session_for(["pluto_v1.cbz", "saga_v11.cbz"], ingest)

    async def exercise():
        await session.run()
        await session.skip("saga_v11.cbz")
        ingest.refuse["discard"] = IngestError("Nineveh is unavailable.")
        await session.skip("pluto_v1.cbz")

    run(exercise())
    assert latest(seen, "saga_v11.cbz").state == "skipped"
    stuck = latest(seen, "pluto_v1.cbz")
    assert (stuck.state, stuck.activity) == (
        "skipped",
        "Skipped, but Nineveh still holds its upload.",
    )


class GatedFinder(TableFinder):
    def __init__(self):
        super().__init__(FINDER.table)
        self.gate = asyncio.Event()

    async def match(self, volume):
        await self.gate.wait()
        return await super().match(volume)


def test_a_volume_skipped_before_its_turn_is_never_prepared():
    finder = GatedFinder()
    files = ["pluto_a.cbz", "pluto_b.cbz", "pluto_c.cbz"]
    session, ingest, seen = session_for(files, finder=finder)

    async def exercise():
        task = asyncio.create_task(session.run())
        while not any(item.id == "pluto_c.cbz" for item in seen):
            await asyncio.sleep(0.001)
        await session.skip("pluto_c.cbz")
        finder.gate.set()
        await task

    run(exercise())
    assert latest(seen, "pluto_c.cbz").state == "skipped"
    assert ("stage", "pluto", "pluto_c.cbz") not in ingest.calls


@pytest.mark.parametrize(
    ("action", "arguments", "message"),
    [
        ("place", ("saga_v11.cbz",), "not ready to place"),
        ("rename", ("saga_v11.cbz", "x.cbz"), "Only a volume that is ready"),
        ("skip", ("gone.cbz",), "no longer part of this review"),
    ],
)
def test_actions_that_do_not_apply_are_refused(action, arguments, message):
    session, _, _ = session_for(["saga_v11.cbz"])

    async def exercise():
        await session.run()
        await getattr(session, action)(*arguments)

    with pytest.raises(ReviewError, match=message):
        run(exercise())


def test_a_volume_being_placed_cannot_be_skipped():
    session, _, seen = session_for(["pluto_v1.cbz"])

    async def exercise():
        await session.run()
        session._items["pluto_v1.cbz"].proposal = latest(
            seen, "pluto_v1.cbz"
        ).__class__(id="pluto_v1.cbz", source="pluto_v1.cbz", state="placing")
        await session.skip("pluto_v1.cbz")

    with pytest.raises(ReviewError, match="still being prepared"):
        run(exercise())


def test_search_offers_series_and_explains_failures():
    session, _, _ = session_for([])
    assert run(session.search("  ")) == ()
    assert run(session.search("saga")) == (
        Candidate("saga", "Saga — Comics · 10 volumes"),
        Candidate("vinland", "Vinland Saga — Manga · 12 volumes"),
    )
    with pytest.raises(ReviewError, match="unavailable"):
        run(session.search("down"))


def test_closing_withdraws_what_was_not_settled_and_goes_quiet():
    ingest = RecordingIngest()
    session, _, seen = session_for(["pluto_a.cbz", "pluto_b.cbz"], ingest)

    async def exercise():
        await session.run()
        await session.place("pluto_a.cbz")
        await session.close()
        await session.close()
        count = len(seen)
        session._emit(session._items["pluto_b.cbz"])
        return count

    count = run(exercise())
    assert discards(ingest) == [("discard", "up-2")]
    assert len(seen) == count


@pytest.mark.parametrize(
    ("sent", "stopped", "withdrawn"),
    [
        # Nineveh never got the whole file: the upload stops, nothing is staged.
        (0.4, ["pluto_v1.cbz"], []),
        # Nineveh already holds it: the upload withdraws itself when it lands.
        (1.0, [], [("discard", "up-1")]),
    ],
)
def test_leaving_mid_upload_leaves_nothing_staged(sent, stopped, withdrawn):
    ingest = GatedIngest(sent)
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        preparing = asyncio.create_task(session.run())
        await ingest.sending.wait()
        preparing.cancel()  # what leaving the board does to its worker
        await asyncio.gather(preparing, return_exceptions=True)
        await session.close()
        shown = len(seen)
        ingest.answer.set()
        await until(lambda: not session._uploads)
        return shown

    shown = run(exercise())
    assert ingest.stopped == stopped
    assert discards(ingest) == withdrawn
    staged = [call for call in ingest.calls if call[0] == "stage"]
    assert len(staged) == len(withdrawn)
    assert len(seen) == shown


def test_quitting_withdraws_what_open_reviews_prepared():
    workflow, session, ingest, _ = review_for(["pluto_a.cbz", "pluto_b.cbz"])

    async def exercise():
        await session.run()
        await session.place("pluto_a.cbz")
        await workflow.aclose()
        await workflow.aclose()

    run(exercise())
    assert discards(ingest) == [("discard", "up-2")]


@pytest.mark.parametrize(
    ("answers", "stopped", "withdrawn"),
    [(True, [], [("discard", "up-1")]), (False, ["pluto_v1.cbz"], [])],
)
def test_quitting_waits_briefly_for_an_upload_nineveh_holds(
    monkeypatch, answers, stopped, withdrawn
):
    monkeypatch.setattr(filing, "SHUTDOWN_GRACE", 0.2)
    ingest = GatedIngest(1.0)
    workflow, session, _, _ = review_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        preparing = asyncio.create_task(session.run())
        await ingest.sending.wait()
        if answers:
            asyncio.get_running_loop().call_later(0.01, ingest.answer.set)
        await workflow.aclose()
        await asyncio.gather(preparing, return_exceptions=True)

    run(exercise())
    assert (ingest.stopped, discards(ingest)) == (stopped, withdrawn)


def test_a_closed_review_starts_no_new_upload():
    session, ingest, _ = session_for(["saga_v11.cbz"])

    async def exercise():
        await session.run()
        await session.close()
        await session._stage(session._items["saga_v11.cbz"], SAGA, "", ())

    run(exercise())
    assert ingest.calls == []


def test_a_folder_too_large_to_read_in_full_says_so():
    limit = "Only the first 1,000 volumes are included."
    inbox = MemoryInbox({"pluto_v1.cbz": (b"PK", None)}, limit=limit)
    workflow = FilingWorkflow(FilingServices(inbox, FINDER, RecordingIngest()))
    assert workflow.describe(FOLDER, True) == f"1 .cbz volume · 2 B · {limit}"
    session = workflow.open(FOLDER, True, lambda proposal: None)
    assert run(session.run()) == limit


def test_an_unreadable_folder_is_explained():
    class MissingInbox(MemoryInbox):
        def scan(self, folder, recursive):
            raise FileNotFoundError(2, "No such file or directory")

    workflow = FilingWorkflow(FilingServices(MissingInbox(), FINDER, RecordingIngest()))
    session = workflow.open(FOLDER, True, lambda proposal: None)
    with pytest.raises(ReviewError, match="Cannot read /downloads: No such file"):
        run(session.run())


@pytest.mark.parametrize(
    ("sizes", "recursive", "expected"),
    [
        ([], False, "No .cbz volumes here. Try including subfolders."),
        ([], True, "No .cbz volumes here."),
        ([500], False, "1 .cbz volume · 500 B"),
        ([1_500, 2_000], False, "2 .cbz volumes · 3.5 KB"),
        ([180_000_000], False, "1 .cbz volume · 180.0 MB"),
        ([1_200_000_000, 1_000_000_000], True, "2 .cbz volumes · 2.2 GB"),
    ],
)
def test_describe_previews_a_folder(sizes, recursive, expected):
    inbox = MemoryInbox(
        {f"v{n}.cbz": (b"x" * size, None) for n, size in enumerate(sizes)}
    )
    workflow = FilingWorkflow(FilingServices(inbox, FINDER, RecordingIngest()))
    assert workflow.title == "File volumes from a folder"
    assert workflow.describe(FOLDER, recursive) == expected


@pytest.mark.parametrize(
    ("staged", "expected"),
    [
        (
            {
                "targetPath": "Manga/Pluto/Pluto 008.cbz",
                "suggestedFilename": "Pluto 008.cbz",
                "siblingPattern": "Pluto NNN.cbz",
                "duplicateOf": {"filename": "Pluto 008.cbz"},
                "pageCount": 200,
            },
            {
                "destination": "Manga/Pluto",
                "filename": "Pluto 008.cbz",
                "pattern": "Pluto NNN.cbz",
                "warning": "Already in the library as Pluto 008.cbz",
                "pages": 200,
            },
        ),
        (
            {"targetPath": "Manga/Pluto", "suggestedFilename": "Pluto 009.cbz"},
            {"destination": "Manga/Pluto", "filename": "Pluto 009.cbz", "warning": ""},
        ),
        (
            {"targetPath": "Comics/Saga/Saga 011.cbz", "pageCount": "lots"},
            {"destination": "Comics/Saga", "filename": "Saga 011.cbz", "pages": 0},
        ),
        ({}, {"destination": "", "filename": "", "pattern": "", "pages": 0}),
    ],
)
def test_a_staged_upload_becomes_a_proposal(staged, expected):
    changes = staged_changes(staged)
    assert changes["state"] == "ready"
    assert {key: changes[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("placed", "expected"),
    [
        (
            {"relativePath": "Manga/Pluto/Pluto 009.cbz"},
            ("Manga/Pluto", "Pluto 009.cbz"),
        ),
        ({}, ("Manga/Pluto", "Pluto 008.cbz")),
    ],
)
def test_a_placed_volume_reports_where_it_landed(placed, expected):
    proposal = Proposal(
        "p", "p.cbz", destination="Manga/Pluto", filename="Pluto 008.cbz"
    )
    changes = placed_changes(placed, proposal)
    assert (changes["destination"], changes["filename"]) == expected
    assert changes["evidence"][0].payload == placed


@pytest.mark.parametrize(
    ("filename", "suggested", "accepted"),
    [
        ("  Pluto 001.cbz ", "Pluto 001.cbz", "Pluto 001.cbz"),
        ("pluto.CBZ", "Pluto 001.cbz", "pluto.CBZ"),
        ("anything", "", "anything"),
    ],
)
def test_a_sensible_rename_is_accepted(filename, suggested, accepted):
    assert checked_filename(filename, suggested) == accepted


@pytest.mark.parametrize(
    ("filename", "suggested", "refusal"),
    [
        ("", "Pluto 001.cbz", "Enter a filename."),
        ("..", "Pluto 001.cbz", "Enter a filename."),
        ("Manga/Pluto 001.cbz", "Pluto 001.cbz", "cannot contain folders"),
        ("a\\b.cbz", "Pluto 001.cbz", "cannot contain folders"),
        (f"{'x' * 252}.cbz", "Pluto 001.cbz", "at most 255"),
        ("Pluto 001.zip", "Pluto 001.cbz", "Keep the .cbz extension."),
    ],
)
def test_a_rename_is_held_to_what_a_filename_can_be(filename, suggested, refusal):
    with pytest.raises(ReviewError, match=refusal):
        checked_filename(filename, suggested)
