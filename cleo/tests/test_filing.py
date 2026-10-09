from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from cleo.domain import MatchResult, SeriesOption
from cleo.filing import (
    CHECKING,
    HELD,
    LEFT,
    PLANNED,
    RECOVERING,
    WITHDRAWN,
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


def review_for(files, ingest=None, finder=FINDER, problems=None, upfront=True):
    inbox = MemoryInbox(
        {name: (b"PK" + name.encode(), None) for name in files}, problems
    )
    ingest = ingest or RecordingIngest()
    seen: list[Proposal] = []
    services = FilingServices(inbox, finder, ingest)
    workflow = FilingWorkflow(services, slots=2, upfront=upfront)
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


# -- Nineveh's queue -------------------------------------------------------------


def queued(ingest_id, name, series="pluto", data=None, **extra):
    """A staged upload as Nineveh lists it; its content defaults to the file's."""
    data = b"PK" + name.encode() if data is None else data
    title = series.title()
    return {
        "ingestId": ingest_id,
        "state": "staged",
        "seriesId": series,
        "filename": name,
        "suggestedFilename": f"{title} 010.cbz",
        "targetPath": f"Manga/{title}/{title} 010.cbz",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "pageCount": 3,
        "duplicateOf": None,
        "createdAt": "2026-10-08T21:30:00+00:00",
        **extra,
    }


def test_a_file_nineveh_already_holds_is_taken_over_not_sent_again():
    ingest = RecordingIngest(
        queue=[
            queued("old-1", "pluto_v1.cbz"),
            queued("old-2", "phone_v10.cbz", data=b"sent from the phone"),
        ]
    )
    session, _, seen = session_for(["pluto_v1.cbz", "pluto_v2.cbz"], ingest)
    run(session.run())
    held = latest(seen, "pluto_v1.cbz")
    assert (held.state, held.carried_over, held.retryable) == ("ready", True, True)
    assert held.subject == Candidate("pluto", "Pluto")
    assert held.subject_note.endswith("waiting in Nineveh's queue")
    phone = latest(seen, "queue:old-2")
    assert (phone.source, phone.state, phone.destination) == (
        "phone_v10.cbz",
        "ready",
        "Manga/Pluto",
    )
    assert (phone.carried_over, phone.retryable) == (True, False)
    assert ingest.calls == [("stage", "pluto", "pluto_v2.cbz")]


def test_an_unreadable_file_is_never_mistaken_for_a_queued_upload():
    record = queued("old-1", "unreadable_v1.cbz")
    ingest = RecordingIngest(queue=[record])
    finder = TableFinder({"unreadable": MatchResult(PLUTO, (), "from the filename")})
    session, _, seen = session_for(["unreadable_v1.cbz"], ingest, finder)
    run(session.run())
    assert latest(seen, "unreadable_v1.cbz").state == "failed"
    assert latest(seen, "queue:old-1").carried_over


def test_uploads_from_before_the_review_stay_unless_withdrawn():
    ingest = RecordingIngest(
        queue=[queued(f"old-{n}", f"{n}.cbz", data=str(n).encode()) for n in (1, 2, 3)]
    )
    session, _, seen = session_for([], ingest)

    async def exercise():
        await session.run()
        await session.skip("queue:old-1")
        await session.withdraw("queue:old-2")
        await session.close()

    run(exercise())
    assert discards(ingest) == [("discard", "old-2")]
    left, withdrawn = latest(seen, "queue:old-1"), latest(seen, "queue:old-2")
    assert (left.state, left.activity) == ("skipped", LEFT)
    assert (withdrawn.state, withdrawn.activity) == ("skipped", WITHDRAWN)


def test_an_upload_from_before_the_review_can_be_placed():
    ingest = RecordingIngest(queue=[queued("old-1", "phone.cbz", data=b"x")])
    session, _, seen = session_for([], ingest)

    async def exercise():
        await session.run()
        await session.place("queue:old-1")

    run(exercise())
    assert ("commit", "old-1", None) in ingest.calls
    assert latest(seen, "queue:old-1").state == "placed"


@pytest.mark.parametrize(
    ("action", "proposal_id", "message"),
    [
        ("withdraw", "pluto_v1.cbz", "Only an upload from before this review"),
        ("choose", "queue:old-1", "no file in this folder to send again"),
    ],
)
def test_what_does_not_apply_to_an_upload_is_refused(action, proposal_id, message):
    ingest = RecordingIngest(queue=[queued("old-1", "phone.cbz", data=b"x")])
    session, _, _ = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        await session.run()
        arguments = (Candidate("saga", "Saga"),) if action == "choose" else ()
        await getattr(session, action)(proposal_id, *arguments)

    with pytest.raises(ReviewError, match=message):
        run(exercise())


def test_an_upload_nineveh_will_not_withdraw_stays_ready():
    ingest = RecordingIngest(queue=[queued("old-1", "phone.cbz", data=b"x")])
    session, _, seen = session_for([], ingest)

    async def exercise():
        await session.run()
        ingest.refuse["discard"] = IngestError("Nineveh is unavailable.")
        await session.withdraw("queue:old-1")

    run(exercise())
    stuck = latest(seen, "queue:old-1")
    assert (stuck.state, stuck.activity) == ("ready", "Nineveh would not withdraw it.")


def test_choosing_another_series_replaces_the_upload_nineveh_held():
    ingest = RecordingIngest(queue=[queued("old-1", "pluto_v1.cbz")])
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)

    async def exercise():
        await session.run()
        await session.choose("pluto_v1.cbz", Candidate("saga", "Saga"))

    run(exercise())
    chosen = latest(seen, "pluto_v1.cbz")
    assert (chosen.state, chosen.carried_over, chosen.subject.id) == (
        "ready",
        False,
        "saga",
    )
    assert ingest.calls == [("discard", "old-1"), ("stage", "saga", "pluto_v1.cbz")]


def test_a_queue_nineveh_will_not_show_is_reported_and_filing_goes_on():
    ingest = RecordingIngest({"pending": IngestError("Nineveh is unavailable.")})
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)
    assert (
        run(session.run()) == "Could not check Nineveh's queue: Nineveh is unavailable."
    )
    assert latest(seen, "pluto_v1.cbz").state == "ready"


# -- the model's suggestion --------------------------------------------------------


@pytest.mark.parametrize(
    ("suggestion", "titles"),
    [
        (
            VINLAND,
            [
                "Vinland Saga — Manga · 12 volumes · suggested",
                "Saga — Comics · 10 volumes",
            ],
        ),
        (None, ["Vinland Saga — Manga · 12 volumes", "Saga — Comics · 10 volumes"]),
    ],
)
def test_a_suggestion_is_offered_first_and_never_acted_on(suggestion, titles):
    finder = TableFinder(
        {"saga": MatchResult(None, (VINLAND, SAGA), "Several.", suggestion)}
    )
    session, ingest, seen = session_for(["saga_v11.cbz"], finder=finder)
    run(session.run())
    proposal = latest(seen, "saga_v11.cbz")
    assert proposal.state == "needs_choice"
    assert proposal.suggestion == (suggestion.subject if suggestion else None)
    assert [item.title for item in proposal.alternatives] == titles
    assert ingest.calls == []


# -- uploading on demand -----------------------------------------------------------


def test_on_demand_a_matched_volume_waits_until_the_user_turns_to_it():
    _, session, ingest, seen = review_for(
        ["pluto_v1.cbz", "saga_v11.cbz"], upfront=False
    )

    async def exercise():
        await session.run()
        planned = latest(seen, "pluto_v1.cbz")
        assert (planned.state, planned.activity) == ("planned", PLANNED)
        assert planned.subject == Candidate("pluto", "Pluto")
        assert ingest.calls == []
        await asyncio.gather(
            session.prepare("pluto_v1.cbz"), session.prepare("pluto_v1.cbz")
        )
        await session.prepare("saga_v11.cbz")
        await session.prepare("gone.cbz")

    run(exercise())
    assert ingest.calls == [("stage", "pluto", "pluto_v1.cbz")]
    assert latest(seen, "pluto_v1.cbz").state == "ready"


def test_on_demand_a_volume_waiting_for_a_slot_is_uploaded_once():
    ingest = GatedIngest(0.5)
    inbox = MemoryInbox(
        {name: (b"PK", None) for name in ("pluto_a.cbz", "pluto_b.cbz")}
    )
    services = FilingServices(inbox, FINDER, ingest)
    session = FilingWorkflow(services, slots=1, upfront=False).open(
        FOLDER, False, lambda proposal: None
    )

    async def exercise():
        await session.run()
        first = asyncio.create_task(session.prepare("pluto_a.cbz"))
        await ingest.sending.wait()  # the only slot is now busy
        waiting = [asyncio.create_task(session.prepare("pluto_b.cbz")) for _ in "ab"]
        await asyncio.sleep(0)
        ingest.answer.set()
        await asyncio.gather(first, *waiting)

    run(exercise())
    uploads = [call for call in ingest.calls if call[0] == "stage"]
    assert [call[2] for call in uploads] == ["pluto_a.cbz", "pluto_b.cbz"]


class DuplicateIngest(RecordingIngest):
    async def stage(self, series_id, filename, content, progress=None):
        staged = await super().stage(series_id, filename, content, progress)
        return {**staged, "duplicateOf": {"filename": "Pluto 001.cbz"}}


@pytest.mark.parametrize(
    ("ingest", "state", "committed"),
    [(RecordingIngest(), "placed", True), (DuplicateIngest(), "ready", False)],
)
def test_on_demand_placing_uploads_first_and_stops_for_a_duplicate(
    ingest, state, committed
):
    _, session, _, seen = review_for(["pluto_v1.cbz"], ingest, upfront=False)

    async def exercise():
        await session.run()
        await session.place("pluto_v1.cbz")

    run(exercise())
    assert latest(seen, "pluto_v1.cbz").state == state
    assert any(call[0] == "commit" for call in ingest.calls) is committed
    assert ingest.calls[0] == ("stage", "pluto", "pluto_v1.cbz")


# -- an answer that never arrived --------------------------------------------------


TIMEOUT = IngestError("Nineveh did not respond in time.")


class LostAnswerIngest(RecordingIngest):
    """Takes the file, loses Nineveh's answer, and lists the upload in its
    queue from the `shows_on`-th lookup (never, when None)."""

    def __init__(self, error, whole=True, shows_on=1, failing=0):
        super().__init__()
        self.error, self.whole, self.shows_on = error, whole, shows_on
        self.failing = failing
        self.late = None
        self.lookups = 0

    async def stage(self, series_id, filename, content, progress=None):
        self.calls.append(("stage", series_id, filename))
        data = content.read() if self.whole else content.read(1)
        self.late = queued("late-1", filename, series_id, data)
        raise self.error

    async def pending(self):
        answer = await super().pending()
        if self.late is not None:
            self.lookups += 1
            if self.lookups <= self.failing:
                raise IngestError("Nineveh is unavailable.")
            if self.shows_on is not None and self.lookups >= self.shows_on:
                answer["pending"].append(self.late)
        return answer


@pytest.mark.parametrize(
    ("error", "whole", "shows_on", "state", "lookups"),
    [
        (TIMEOUT, True, 1, "ready", 1),
        (TIMEOUT, True, 3, "ready", 3),
        (TIMEOUT, True, None, "failed", 3),
        # The first lookup itself fails; the next one finds it.
        ((TIMEOUT, 1), True, 2, "ready", 2),
        (
            IngestError("Nineveh could not accept that volume.", 422),
            True,
            1,
            "failed",
            0,
        ),
        (TIMEOUT, False, 1, "failed", 0),
    ],
)
def test_an_upload_whose_answer_was_lost_is_found_in_the_queue(
    monkeypatch, error, whole, shows_on, state, lookups
):
    monkeypatch.setattr(filing, "RECOVERY_DELAY", 0)
    error, failing = error if isinstance(error, tuple) else (error, 0)
    ingest = LostAnswerIngest(error, whole, shows_on, failing)
    session, _, seen = session_for(["pluto_v1.cbz"], ingest)
    run(session.run())
    proposal = latest(seen, "pluto_v1.cbz")
    assert (proposal.state, ingest.lookups) == (state, lookups)
    looked = RECOVERING in [item.activity for item in seen]
    assert looked is (lookups > 0)
    if state == "ready":
        assert session._items["pluto_v1.cbz"].ingest_id == "late-1"
        assert not proposal.carried_over
    else:
        assert proposal.activity == str(error)
