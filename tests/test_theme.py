"""Colour themes: Olympus's own, the palette Rich text uses, and the user's choice."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from rich.style import Style
from rich.text import Text
from shell_fakes import ScriptedWorkflow, configured, sample_proposals, type_and_send
from textual.app import App
from textual.widgets import ListView, Static

from olympus.app import OlympusApp
from olympus.config import Settings
from olympus.services import AgentService
from olympus.store import OlympusStore
from olympus.ui import proposals as view
from olympus.ui.review import ReviewBoardScreen
from olympus.ui.theme import DEFAULT_PALETTE, OLYMPUS, Palette, theme_variables

ROOT = Path(__file__).resolve().parents[1]
SIZE = (120, 45)


def test_only_the_theme_module_names_colours():
    """Anything else would ignore the chosen theme."""
    named = {
        path.relative_to(ROOT).as_posix(): re.findall(r"#[0-9a-fA-F]{6}\b", text)
        for path in [*ROOT.glob("src/olympus/**/*.py"), *ROOT.glob("cleo/src/**/*.py")]
        if path.name != "theme.py" and (text := path.read_text(encoding="utf-8"))
    }
    assert {path: found for path, found in named.items() if found} == {}


def test_the_default_palette_keeps_the_colours_olympus_always_had():
    assert DEFAULT_PALETTE == Palette(
        text="#E6EDF3",
        muted="#8E949B",
        faint="#62686F",
        info="#8BD5FF",
        success="#3FB950",
        warning="#D29922",
        error="#F85149",
        choice="#D2A8FF",
        carried="#56D4DD",
    )


@pytest.mark.parametrize(
    ("style", "resolved"),
    [
        ("success", "#3FB950"),
        ("bold success", "bold #3FB950"),
        ("bold text", "bold #E6EDF3"),
        ("bold", "bold"),
        ("", ""),
    ],
)
def test_a_style_names_roles_that_the_palette_makes_concrete(style, resolved):
    assert DEFAULT_PALETTE.style(style) == resolved


def test_each_theme_brings_its_own_palette():
    light = Palette.from_variables(
        theme_variables(App().available_themes["textual-light"])
    )
    assert light != DEFAULT_PALETTE
    # Text on a light background is dark.
    assert int(light.text[1:3], 16) < 0x80


def test_rows_tallies_and_details_draw_in_the_palette_they_are_given():
    proposal = sample_proposals()[0]
    other = Palette(*(f"#{n:02X}{n:02X}{n:02X}" for n in range(1, 10)))
    row = view.row_text(proposal, other)
    assert _colours(row) == {other.success}
    assert other.success in _colours(view.tally_text([proposal], other))
    heading, facts, _, _ = view.detail_renderable(proposal, other).renderables
    assert (Style.parse(heading.style).color.name, facts.style) == (
        other.text.lower(),
        other.muted,
    )


def test_ctrl_k_offers_textual_commands_and_one_quit(tmp_path):
    app, _, _, _ = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            titles = [command.title for command in app.get_system_commands(app.screen)]
            assert {"Theme", "Keys", "Screenshot"} <= set(titles)
            assert [title for title in titles if "Quit" in title] == ["Quit Olympus"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("saved", "starts_in"),
    [(None, OLYMPUS.name), ("nord", "nord"), ("no-such-theme", OLYMPUS.name)],
)
def test_the_chosen_theme_is_kept_between_launches(tmp_path, saved, starts_in):
    state = tmp_path / "state"
    if saved is not None:
        store = OlympusStore(state)
        store.save_theme(saved)
        store.close()
    app, _, _, _ = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert app.theme == starts_in
            app.theme = "gruvbox"
            await pilot.pause()

    asyncio.run(exercise())
    reopened = OlympusStore(state)
    assert reopened.theme() == "gruvbox"
    service = AgentService(reopened, app._service.registry, app._service.secrets)
    assert OlympusApp(service, Settings(state)).theme == "gruvbox"
    reopened.close()


def test_the_review_board_redraws_in_a_new_theme(tmp_path):
    app, _, provider, _ = configured(tmp_path)
    provider.workflow = ScriptedWorkflow(sample_proposals())
    (tmp_path / "inbox").mkdir()

    async def exercise():
        async with app.run_test(size=SIZE) as pilot:
            await type_and_send(pilot, f"/file {tmp_path / 'inbox'}")
            await pilot.click("#folder-start")
            for _ in range(4):
                await pilot.pause()
            board = app.screen
            assert isinstance(board, ReviewBoardScreen)
            first = board.query_one("#board-files", ListView).children[0]
            assert _colours(first.query_one(Static).content) == {
                DEFAULT_PALETTE.success
            }
            app.theme = "textual-light"
            for _ in range(4):
                await pilot.pause()
            light = Palette.from_variables(app.get_css_variables())
            assert _colours(first.query_one(Static).content) == {light.success}

    asyncio.run(exercise())


def _colours(text: Text) -> set[str]:
    """The foreground colours a Rich Text uses, as #RRGGBB."""
    styles = (
        Style.parse(span.style) if isinstance(span.style, str) else span.style
        for span in text.spans
    )
    return {
        style.color.triplet.hex.upper()
        for style in styles
        if style.color is not None and style.color.triplet is not None
    }
