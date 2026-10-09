from __future__ import annotations

from datetime import UTC

import pytest

from olympus import cli, config
from olympus.commands import Command, parse_command
from olympus.ports import StoreError
from olympus.presentation import format_timestamp, human_size, plain_text, streamable


def test_commands_and_terminal_safe_formatting():
    assert parse_command("hello") is None
    assert parse_command(" /AGENT   Cleo ") == Command("agent", "Cleo")
    assert plain_text("safe\x1b[31m red") == "safe red"
    assert streamable("safe\x1b[") == "safe"
    assert format_timestamp("not-a-time") == "not-a-time"
    assert "2025" in format_timestamp("2025-01-02T03:04:00+00:00", UTC)


def test_non_macos_state_locations(monkeypatch, tmp_path):
    monkeypatch.delenv("OLYMPUS_STATE_DIR", raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert config.default_state_dir() == tmp_path / "olympus"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    assert config.default_state_dir() == tmp_path / ".local/share/olympus"


def test_cli_launches_and_formats_startup_failures(monkeypatch, tmp_path):
    launched = []

    class App:
        def run(self):
            launched.append(True)

    monkeypatch.setenv("OLYMPUS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "build_app", lambda settings: App())
    cli.main(())
    assert launched == [True]

    def fail(settings):
        raise StoreError("broken state")

    monkeypatch.setattr(cli, "build_app", fail)
    try:
        cli.main(())
    except SystemExit as error:
        assert str(error) == "olympus: broken state"
    else:  # pragma: no cover - assertion aid
        raise AssertionError("startup failure did not exit")


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1_000, "1.0 KB"),
        (1_500, "1.5 KB"),
        (182_000_000, "182.0 MB"),
        (2_400_000_000, "2.4 GB"),
        (4_000_000_000, "4.0 GB"),
        (5_000_000_000_000, "5000.0 GB"),
    ],
)
def test_human_size(size, expected):
    assert human_size(size) == expected
