"""Shortcuts pressed the way a user presses them: with the composer focused."""

from __future__ import annotations

import asyncio

from shell_fakes import configured, finish_replies, transcript_text
from textual.command import CommandPalette
from textual.widgets import Input, TextArea

from olympus.ui.screens import (
    AgentFormScreen,
    AgentManagerScreen,
    HistoryScreen,
    IdentityScreen,
)


def run(tmp_path, exercise, count=2, size=(110, 45)):
    app, _service, _provider, profiles = configured(tmp_path, count)

    async def main():
        async with app.run_test(size=size) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.focus()
            composer.text = "typed"
            await exercise(app, pilot, composer, profiles)

    asyncio.run(main())


def test_ctrl_a_opens_agents_from_the_composer_and_never_stacks(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert isinstance(app.screen, AgentManagerScreen)
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert composer.text == "typed"

    run(tmp_path, exercise)


def test_workspace_shortcuts_step_aside_on_other_screens(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, AgentFormScreen)
        name = app.screen.query_one("#agent-name", Input)
        name.focus()
        name.cursor_position = len(name.value)
        await pilot.press("ctrl+a")
        await pilot.press("X")
        assert name.value.startswith("X")
        for key in ("ctrl+k", "ctrl+r", "ctrl+n", "ctrl+s", "ctrl+g"):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, AgentFormScreen), key

    run(tmp_path, exercise)


def test_ctrl_k_opens_the_palette_from_the_composer(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"Switch to Oracle 2")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app._profile.id == profiles[1].id

    run(tmp_path, exercise)


def test_history_new_and_style_shortcuts(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        composer.text = "worth keeping"
        await pilot.press("enter")
        await finish_replies(app, pilot)
        first = app._conversation.id

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app._conversation.id != first
        assert "worth keeping" not in transcript_text(app)

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert isinstance(app.screen, HistoryScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, IdentityScreen)

    run(tmp_path, exercise)


def test_keyboard_switching_is_portable(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        await pilot.press("ctrl+g")
        assert app.focused is app.query_one("#agent-list")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app._profile.id == profiles[1].id
        assert app.focused is composer

        await pilot.press("ctrl+1")
        await pilot.pause()
        await pilot.pause()
        assert app._profile.id == profiles[0].id

        await pilot.press("ctrl+b")
        assert not app.query_one("#sidebar").display
        await pilot.press("ctrl+b")
        assert app.query_one("#sidebar").display

    run(tmp_path, exercise)


def test_newlines_come_from_shift_enter_or_the_portable_ctrl_j(tmp_path):
    async def exercise(app, pilot, composer, profiles):
        composer.text = ""
        await pilot.press("a", "shift+enter", "b", "ctrl+j", "c")
        assert composer.text == "a\nb\nc"

    run(tmp_path, exercise)
