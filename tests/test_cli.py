"""The `olympus` command, its configuration, and the development bootstrap."""

from __future__ import annotations

import pytest
from fake_backends import FAKE_TOKEN_VARIABLE

from olympus import cli, config
from olympus.config import Settings
from olympus.ports import ProviderError
from olympus.secrets import environment_key


def test_configuration_uses_macos_data_location_and_environment_overrides(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("OLYMPUS_STATE_DIR", raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    assert (
        config.default_state_dir() == tmp_path / "Library/Application Support/Olympus"
    )

    chosen = tmp_path / "chosen"
    monkeypatch.setenv("OLYMPUS_STATE_DIR", str(chosen))
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test/")
    monkeypatch.setenv("OLYMPUS_MODEL", "model-x")
    assert Settings.load() == Settings(chosen, "http://ollama.test", "model-x")


def test_cli_reports_version_and_data_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OLYMPUS_STATE_DIR", str(tmp_path))
    cli.main(("--data-dir",))
    assert capsys.readouterr().out.strip() == str(tmp_path)
    with pytest.raises(SystemExit, match="0"):
        cli.main(("--version",))
    assert "Olympus 0.1.0" in capsys.readouterr().out


def test_unusable_state_exits_with_a_message_not_a_traceback(monkeypatch, tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    monkeypatch.setenv("OLYMPUS_STATE_DIR", str(occupied))
    with pytest.raises(SystemExit, match="^olympus: Olympus could not open"):
        cli.main(())

    monkeypatch.setattr(
        cli, "build_app", lambda settings: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(SystemExit, match="^olympus: disk full"):
        cli.main(())


def test_the_fake_bootstrap_hands_its_token_over_without_storing_it(
    monkeypatch, tmp_path
):
    assert FAKE_TOKEN_VARIABLE == environment_key(
        cli.BOOTSTRAP_AGENT_ID, "nineveh_token"
    )
    monkeypatch.setenv("OLYMPUS_BOOTSTRAP_CLEO", "1")
    monkeypatch.setenv(FAKE_TOKEN_VARIABLE, "nvh_fake")
    monkeypatch.setenv("NINEVEH_URL", "http://nineveh.test")
    monkeypatch.setattr("olympus.secrets.sys.platform", "linux")
    app = cli.build_app(Settings(tmp_path))
    [profile] = app._service.store.agents()
    assert (profile.id, profile.kind) == (cli.BOOTSTRAP_AGENT_ID, "cleo")
    assert profile.settings["nineveh_url"] == "http://nineveh.test"
    assert app._service.secrets.location(profile.id, "nineveh_token") == "environment"
    assert not (tmp_path / "secrets.json").exists()
    app._service.store.close()


def test_a_failed_bootstrap_closes_the_store_and_ignores_nineveh_token(
    monkeypatch, tmp_path
):
    closed = []
    original = cli.OlympusStore

    class TrackedStore(original):
        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(cli, "OlympusStore", TrackedStore)
    monkeypatch.setattr("olympus.secrets.sys.platform", "linux")
    monkeypatch.setenv("OLYMPUS_BOOTSTRAP_CLEO", "1")
    monkeypatch.setenv("NINEVEH_TOKEN", "a token for every agent")
    monkeypatch.delenv(FAKE_TOKEN_VARIABLE, raising=False)
    with pytest.raises(ProviderError, match="token"):
        cli.build_app(Settings(tmp_path))
    assert closed == [True]
