"""The folder picker, the review board, and how the shell files the outcome."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.console import Console
from shell_fakes import (
    HangingSession,
    OfferingSession,
    ScriptedWorkflow,
    configured,
    evidence_titles,
    finish_replies,
    notifications,
    sample_proposals,
    started,
    transcript_text,
    type_and_send,
)
from textual.widgets import Button, Input, ListView, Static, Switch

from olympus.domain import Candidate, Proposal
from olympus.ports import ReviewError
from olympus.ui import review
from olympus.ui.folders import FolderPickerScreen, FolderRequest
from olympus.ui.review import RenameScreen, ReviewBoardScreen, SeriesPickerScreen
from olympus.ui.screens import ConfirmScreen

SIZE = (120, 45)


def with_workflow(tmp_path, workflow=None):
    app, service, provider, profiles = configured(tmp_path)
    provider.workflow = workflow or ScriptedWorkflow(sample_proposals())
    (tmp_path / "inbox").mkdir()
    return app, service, provider, profiles


async def settle(pilot, rounds=4):
    for _ in range(rounds):
        await pilot.pause()


async def until(pilot, predicate, rounds=100):
    """Pause until `predicate` holds, rather than for a fixed number of rounds."""
    for _ in range(rounds):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError("condition never held")


async def open_board(app, pilot, folder):
    await type_and_send(pilot, f"/file {folder}")
    assert isinstance(app.screen, FolderPickerScreen)
    await pilot.click("#folder-start")
    await settle(pilot)
    assert isinstance(app.screen, ReviewBoardScreen)
    return app.screen


def card_text(board) -> str:
    console = Console(width=100, record=True, color_system=None)
    console.print(board.query_one("#board-card", Static).content)
    return console.export_text()


def tally_text(board) -> str:
    return str(board.query_one("#board-tally", Static).render())


async def highlight(board, pilot, index):
    board.query_one("#board-files", ListView).index = index
    await pilot.pause()


def test_the_picker_previews_a_folder_and_starts_a_review(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)
    workflow = provider.workflow

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("ctrl+o")
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, FolderPickerScreen)
            path = picker.query_one("#folder-path", Input)
            assert path.value == f"{tmp_path}/"
            path.value = str(tmp_path / "missing")
            await pilot.press("enter")
            assert "Choose an existing folder." in str(
                picker.query_one("#folder-preview", Static).render()
            )
            path.value = str(tmp_path / "inbox")
            picker.query_one("#folder-recursive", Switch).value = True
            await pilot.pause(0.4)
            assert workflow.described[-1] == ((tmp_path / "inbox").resolve(), True)
            assert "3 things here" in str(
                picker.query_one("#folder-preview", Static).render()
            )
            await pilot.click("#folder-start")
            await settle(pilot)
            assert isinstance(app.screen, ReviewBoardScreen)
            folder, recursive, _ = workflow.sessions[0]
            assert (folder, recursive) == ((tmp_path / "inbox").resolve(), True)

    asyncio.run(exercise())


def test_the_picker_can_be_cancelled_and_reports_unreadable_folders(tmp_path):
    class UnreadableWorkflow(ScriptedWorkflow):
        def describe(self, folder, recursive):
            raise PermissionError(13, "Permission denied")

    app, _, _, _ = with_workflow(tmp_path, UnreadableWorkflow())

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await type_and_send(pilot, f"/file {tmp_path / 'inbox'}")
            await pilot.pause(0.4)
            preview = str(app.screen.query_one("#folder-preview", Static).render())
            assert preview == "Cannot read that folder: Permission denied"
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, FolderPickerScreen)

    asyncio.run(exercise())


def test_the_board_shows_every_proposal_and_the_highlighted_one_in_detail(tmp_path):
    app, _, _, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            assert tally_text(board) == "3 files   1 ready   1 to check   1 need you"
            assert "Series One 001.cbz" in card_text(board)
            assert "2.0 MB · 10 pages" in card_text(board)
            assert not board.query_one("#board-place", Button).disabled
            await highlight(board, pilot, 2)
            assert "Not sure yet" in card_text(board)
            assert board.query_one("#board-place", Button).disabled
            assert not board.query_one("#board-choose", Button).disabled

    asyncio.run(exercise())


def test_decisions_reach_the_agent_and_place_all_skips_warnings(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await pilot.press("A")
            await settle(pilot)
            assert session.actions == [("place", "a.cbz")]
            await highlight(board, pilot, 1)
            await pilot.press("s")
            await settle(pilot)
            assert session.actions[-1] == ("skip", "b.cbz")
            await pilot.press("A")
            await pilot.pause()
            assert "Nothing is ready to place without a check." in notifications(app)
            assert tally_text(board) == "3 files   1 placed   1 need you   1 skipped"
            await highlight(board, pilot, 2)
            await pilot.click("#board-place")
            await pilot.press("r")
            await pilot.pause()
            assert len(session.actions) == 2

    asyncio.run(exercise())


def test_choosing_a_series_searches_and_restages(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await highlight(board, pilot, 2)
            await pilot.press("c")
            await settle(pilot)
            picker = app.screen
            assert isinstance(picker, SeriesPickerScreen)
            results = picker.query_one("#series-list", ListView)
            assert len(results.children) == 2
            search = picker.query_one("#series-search", Input)
            search.value = "down"
            await pilot.pause(0.4)
            await settle(pilot)
            status = str(picker.query_one("#series-status", Static).render())
            assert status == "Search failed."
            search.value = "Saga"
            await pilot.pause(0.4)
            await settle(pilot)
            assert "1 match" in str(picker.query_one("#series-status", Static).render())
            await pilot.press("down", "enter")
            await settle(pilot)
            assert session.actions[-1] == ("choose", "c.cbz", "found")
            assert "Saga found" in card_text(board)

    asyncio.run(exercise())


def test_the_series_picker_can_choose_by_button_or_be_cancelled(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await highlight(board, pilot, 2)
            await pilot.click("#board-choose")
            await settle(pilot)
            await pilot.press("enter")  # with suggestions shown, Enter moves to them
            await pilot.click("#series-choose")
            await settle(pilot)
            assert session.actions[-1] == ("choose", "c.cbz", "s1")
            await pilot.press("c")
            await settle(pilot)
            await pilot.click("#series-cancel")
            await settle(pilot)
            assert isinstance(app.screen, ReviewBoardScreen)
            assert [a[0] for a in session.actions].count("choose") == 1

    asyncio.run(exercise())


def test_an_empty_search_list_searches_on_enter(tmp_path):
    async def search(query):
        return ()

    picker = SeriesPickerScreen("x.cbz", (), search)
    app, _, _, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            app.push_screen(picker)
            await settle(pilot)
            assert "No suggestions yet" in str(
                picker.query_one("#series-status", Static).render()
            )
            picker.query_one("#series-search", Input).value = "nothing"
            await pilot.press("enter")
            await settle(pilot)
            assert "0 matches" in str(
                picker.query_one("#series-status", Static).render()
            )

    asyncio.run(exercise())


def test_renaming_goes_through_the_agent_which_may_refuse(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await pilot.click("#board-rename")
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, RenameScreen)
            assert (
                dialog.query_one("#rename-input", Input).value == "Series One 001.cbz"
            )
            dialog.query_one("#rename-input", Input).value = "Mine.cbz"
            await pilot.press("enter")
            await until(pilot, lambda: "Mine.cbz" in card_text(board))
            assert session.actions[-1] == ("rename", "a.cbz", "Mine.cbz")
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, RenameScreen))
            app.screen.query_one("#rename-input", Input).value = "bad"
            await pilot.click("#rename-save")
            await until(pilot, lambda: "That name will not do." in notifications(app))
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, RenameScreen))
            await pilot.click("#rename-cancel")
            await until(pilot, lambda: app.screen is board)
            assert len(session.actions) == 2

    asyncio.run(exercise())


def test_leaving_confirms_then_files_a_summary_in_the_conversation(tmp_path):
    app, service, provider, profiles = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#cancel")
            await pilot.pause()
            assert isinstance(app.screen, ReviewBoardScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.click("#confirm")
            await settle(pilot, 8)
            assert session.closed
            assert not isinstance(app.screen, ReviewBoardScreen)
            text = transcript_text(app)
            assert f"File things: {(tmp_path / 'inbox').resolve()}" in text
            assert "Placed 1 of 3 files." in text
            assert "✓ `a.cbz`" in text
            assert any("folder_review" in title for title in evidence_titles(app))
            store = service.store.for_agent(profiles[0].id)
            assert store.has_evidence(app._conversation.id)

    asyncio.run(exercise())


def test_a_file_that_cannot_be_uploaded_can_only_be_skipped(tmp_path):
    too_large = Proposal(
        "huge.cbz",
        "huge.cbz",
        "failed",
        activity="Larger than the 4.0 GB limit.",
        retryable=False,
    )
    app, _, provider, _ = with_workflow(tmp_path, ScriptedWorkflow((too_large,)))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            assert "Larger than the 4.0 GB limit." in card_text(board)
            assert board.query_one("#board-choose", Button).disabled
            assert not board.query_one("#board-skip", Button).disabled
            await pilot.press("enter")
            await pilot.press("c")
            await settle(pilot)
            assert app.screen is board
            assert session.actions == []
            await pilot.press("s")
            await until(pilot, lambda: session.actions == [("skip", "huge.cbz")])

    asyncio.run(exercise())


def test_a_review_shares_what_it_could_not_cover(tmp_path):
    notice = "Only the first 1,000 volumes are included."
    workflow = ScriptedWorkflow(sample_proposals(), notice=notice)
    app, _, _, _ = with_workflow(tmp_path, workflow)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            await until(pilot, lambda: notice in notifications(app))

    asyncio.run(exercise())


def test_a_board_that_is_gone_ignores_late_snapshots():
    """Quitting unmounts the board while an upload may still report back."""
    board = ReviewBoardScreen(ScriptedWorkflow(), FolderRequest(Path("/in")), "Cleo")
    board.show_proposal(sample_proposals()[0])
    assert board.proposals == []


def test_an_empty_folder_leaves_without_asking_or_filing(tmp_path):
    app, _, _, _ = with_workflow(tmp_path, ScriptedWorkflow())

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            assert tally_text(board) == "Nothing to file in this folder."
            assert "Nothing here" in card_text(board)
            await pilot.press("escape")
            await settle(pilot)
            assert not isinstance(app.screen, ReviewBoardScreen)
            assert "Nothing to file" not in transcript_text(app)
            assert "File things" not in transcript_text(app)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (ReviewError("Cannot read that folder."), "Cannot read that folder."),
        (RuntimeError(""), "RuntimeError"),
    ],
)
def test_a_review_that_fails_to_start_is_reported(tmp_path, failure, message):
    app, _, _, _ = with_workflow(tmp_path, ScriptedWorkflow(fail_run=failure))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            await settle(pilot)
            assert message in notifications(app)

    asyncio.run(exercise())


def test_an_agent_without_a_workflow_explains_how_to_get_one(tmp_path):
    app, _, _, _ = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await type_and_send(pilot, "/file")
            assert not isinstance(app.screen, FolderPickerScreen)
            assert any("cannot file from a folder" in n for n in notifications(app))
            commands = [
                command.title for command in app.get_system_commands(app.screen)
            ]
            assert not any("File things" in title for title in commands)

    asyncio.run(exercise())


def test_the_palette_offers_the_workflow(tmp_path):
    app, _, _, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            commands = {c.title: c for c in app.get_system_commands(app.screen)}
            assert "File things…" in commands
            commands["File things…"].callback()
            await pilot.pause()
            assert isinstance(app.screen, FolderPickerScreen)

    asyncio.run(exercise())


def test_an_agents_offer_becomes_a_button_that_opens_the_picker(tmp_path):
    app, _, provider, profiles = with_workflow(tmp_path)
    folder = tmp_path / "inbox"
    provider.sessions[profiles[0].id] = OfferingSession(folder)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await type_and_send(pilot, "file my downloads")
            await finish_replies(app, pilot)
            assert f"I can review {folder}." in transcript_text(app)
            await pilot.click(".offer")
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, FolderPickerScreen)
            assert picker.query_one("#folder-path", Input).value == str(folder)

    asyncio.run(exercise())


def test_a_review_waits_for_the_reply_on_screen(tmp_path):
    app, _, provider, profiles = with_workflow(tmp_path)
    session = provider.sessions[profiles[0].id] = HangingSession()

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await type_and_send(pilot, "question")
            await started(session)
            app.action_review_folder()
            await pilot.pause()
            assert not isinstance(app.screen, FolderPickerScreen)
            assert "Cancel or wait for this response." in notifications(app)
            session.release.set()
            await finish_replies(app, pilot)

    asyncio.run(exercise())


def test_clicking_a_file_only_highlights_it(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            rows = list(board.query_one("#board-files", ListView).children)
            for index in (2, 0):  # a file needing a series, then a ready one
                await pilot.click(rows[index])
                await settle(pilot)
                assert board._highlighted().id == rows[index].proposal_id
                assert app.screen is board
            assert session.actions == []
            await pilot.press("enter")
            await until(pilot, lambda: session.actions == [("place", "a.cbz")])

    asyncio.run(exercise())


def test_enter_chooses_a_series_when_there_is_nothing_to_place(tmp_path):
    app, _, _, _ = with_workflow(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            await highlight(board, pilot, 2)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, SeriesPickerScreen)

    asyncio.run(exercise())


def test_leaving_lets_a_placement_in_progress_finish(tmp_path):
    gate = asyncio.Event()

    class SlowPlacing(ScriptedWorkflow):
        def open(self, folder, recursive, listener):
            session = super().open(folder, recursive, listener)
            original = session.place

            async def place(proposal_id):
                await gate.wait()
                await original(proposal_id)

            session.place = place
            return session

    app, _, provider, _ = with_workflow(tmp_path, SlowPlacing(sample_proposals()))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.click("#confirm")
            await settle(pilot)
            assert isinstance(app.screen, ReviewBoardScreen)
            gate.set()
            await settle(pilot, 8)
            assert not isinstance(app.screen, ReviewBoardScreen)
            assert "✓ `a.cbz`" in transcript_text(app)
            assert provider.workflow.sessions[0][2].closed

    asyncio.run(exercise())


def queue_and_plan():
    return (
        Proposal("a.cbz", "a.cbz", "planned", subject=Candidate("s1", "Series One")),
        Proposal("b.cbz", "b.cbz", "needs_choice"),
        Proposal(
            "queue:1",
            "phone.cbz",
            "ready",
            subject=Candidate("s1", "Series One"),
            subject_note="Manga · waiting in the queue",
            destination="Manga/Series One",
            filename="Series One 010.cbz",
            carried_over=True,
            retryable=False,
        ),
    )


def test_an_upload_from_before_the_review_sits_apart_and_can_be_withdrawn(tmp_path):
    app, _, provider, _ = with_workflow(tmp_path, ScriptedWorkflow(queue_and_plan()))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            files = board.query_one("#board-files", ListView)
            labels = [str(row.query_one(Static).render()) for row in files.children]
            assert labels[2] == "From before this review"
            assert files.children[2].disabled
            assert "1 from earlier" in tally_text(board)
            await highlight(board, pilot, 3)
            assert "waiting in the queue" in card_text(board)
            assert not board.query_one("#board-withdraw", Button).disabled
            assert board.query_one("#board-choose", Button).disabled
            await pilot.press("w")
            await until(pilot, lambda: ("withdraw", "queue:1") in session.actions)
            await highlight(board, pilot, 1)
            assert board.query_one("#board-withdraw", Button).disabled

    asyncio.run(exercise())


def test_a_planned_file_is_prepared_once_the_user_stays_on_it(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DWELL", 0.05)
    app, _, provider, _ = with_workflow(tmp_path, ScriptedWorkflow(queue_and_plan()))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            board = await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            # Opening the board rests on the first file; that is not a choice.
            await pilot.pause(0.2)
            assert ("prepare", "a.cbz") not in session.actions
            # Passing over the planned file starts nothing either.
            await highlight(board, pilot, 1)
            await highlight(board, pilot, 0)
            await highlight(board, pilot, 1)
            await pilot.pause(0.2)
            assert ("prepare", "a.cbz") not in session.actions
            await highlight(board, pilot, 0)
            await until(pilot, lambda: ("prepare", "a.cbz") in session.actions)
            await pilot.pause(0.2)
            assert session.actions.count(("prepare", "a.cbz")) == 1
            assert "Lib/Prepared" in card_text(board)

    asyncio.run(exercise())


def test_enter_places_a_planned_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DWELL", 60)
    app, _, provider, _ = with_workflow(tmp_path, ScriptedWorkflow(queue_and_plan()))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await pilot.press("enter")
            await until(pilot, lambda: ("place", "a.cbz") in session.actions)

    asyncio.run(exercise())


def test_leaving_keeps_uploads_from_before_without_asking(tmp_path):
    earlier = queue_and_plan()[2]
    app, _, provider, _ = with_workflow(tmp_path, ScriptedWorkflow((earlier,)))

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await open_board(app, pilot, tmp_path / "inbox")
            session = provider.workflow.sessions[0][2]
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, ReviewBoardScreen))
            assert session.closed
            assert "left waiting, as it was before" in transcript_text(app)

    asyncio.run(exercise())
