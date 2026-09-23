"""The composition root: what `cleo` does before any conversation starts."""

from __future__ import annotations

import pytest

from cleo import config
from cleo.app import CleoApp, build_app, main
from cleo.config import Settings


def isolated(monkeypatch, tmp_path):
    """No inherited .env and no inherited environment for the loader to find."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent.env")
    for key in ("NINEVEH_TOKEN", "NINEVEH_URL", "OLLAMA_URL", "CLEO_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLEO_STATE_DIR", str(tmp_path / "state"))


def test_a_missing_token_exits_with_the_fix_rather_than_a_traceback(
    monkeypatch, tmp_path
):
    isolated(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="cleo: No NINEVEH_TOKEN"):
        main()


def test_an_unusable_state_directory_exits_the_same_way(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("NINEVEH_TOKEN", "nvh_test")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "state").symlink_to(elsewhere)
    with pytest.raises(SystemExit, match="cleo: .*symbolic link"):
        main()


def test_the_running_app_is_wired_to_the_configured_model(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("NINEVEH_TOKEN", "nvh_test")
    monkeypatch.setenv("CLEO_MODEL", "ornith-1.5:35b")
    launched: list[str] = []
    monkeypatch.setattr(CleoApp, "run", lambda self: launched.append(self._model))
    main()
    assert launched == ["ornith-1.5:35b"]


def test_build_app_reaches_nineveh_and_ollama_and_nothing_else(tmp_path):
    settings = Settings(
        nineveh_url="http://nineveh.test",
        token="nvh_test",
        ollama_url="http://ollama.test",
        model="granite4.2:8b",
        state_dir=tmp_path / "state",
    )
    app = build_app(settings)
    assert app._model == "granite4.2:8b"
    assert len(app._close_async) == 2
    app._store.close()
