"""Loading the Nineveh credential, and not leaking it."""

from __future__ import annotations

from pathlib import Path

import pytest

from cleo.config import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_URL,
    ConfigurationError,
    Settings,
    read_env_file,
)


def write_env(tmp_path: Path, body: str, mode: int = 0o600) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    path.chmod(mode)
    return path


def test_values_come_from_the_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NINEVEH_TOKEN", raising=False)
    monkeypatch.delenv("NINEVEH_URL", raising=False)
    env = write_env(tmp_path, "NINEVEH_URL=http://nas:8080\nNINEVEH_TOKEN=nvh_abc\n")
    settings = Settings.load(env)
    assert settings.nineveh_url == "http://nas:8080"
    assert settings.token == "nvh_abc"


def test_the_environment_wins_over_the_file(tmp_path: Path, monkeypatch):
    """So a container can supply the token without shipping a file."""
    env = write_env(tmp_path, "NINEVEH_URL=http://from-file\nNINEVEH_TOKEN=from-file\n")
    monkeypatch.setenv("NINEVEH_TOKEN", "from-environment")
    monkeypatch.setenv("NINEVEH_URL", "http://from-environment")
    settings = Settings.load(env)
    assert settings.token == "from-environment"
    assert settings.nineveh_url == "http://from-environment"


def test_a_missing_token_says_what_to_do(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NINEVEH_TOKEN", raising=False)
    env = write_env(tmp_path, "NINEVEH_URL=http://nas:8080\n")
    with pytest.raises(ConfigurationError) as raised:
        Settings.load(env)
    assert "Admin -> Librarian" in str(raised.value)


def test_the_url_falls_back_to_localhost(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NINEVEH_URL", raising=False)
    env = write_env(tmp_path, "NINEVEH_TOKEN=nvh_abc\n")
    assert Settings.load(env).nineveh_url == DEFAULT_URL


def test_a_trailing_slash_is_dropped(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NINEVEH_URL", raising=False)
    env = write_env(tmp_path, "NINEVEH_URL=http://nas:8080/\nNINEVEH_TOKEN=nvh_abc\n")
    assert Settings.load(env).nineveh_url == "http://nas:8080"


def test_local_model_settings_have_safe_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("CLEO_MODEL", raising=False)
    env = write_env(tmp_path, "NINEVEH_TOKEN=nvh_abc\n")
    settings = Settings.load(env)
    assert settings.ollama_url == DEFAULT_OLLAMA_URL
    assert settings.model == DEFAULT_MODEL


def test_model_and_private_state_location_can_be_configured(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("CLEO_MODEL", raising=False)
    monkeypatch.delenv("CLEO_STATE_DIR", raising=False)
    state = tmp_path / "state"
    env = write_env(
        tmp_path,
        "NINEVEH_TOKEN=x\n"
        "OLLAMA_URL=http://ollama/\n"
        "CLEO_MODEL=another-model\n"
        f"CLEO_STATE_DIR={state}\n",
    )
    settings = Settings.load(env)
    assert settings.ollama_url == "http://ollama"
    assert settings.model == "another-model"
    assert settings.data_dir == state


def test_the_token_never_appears_in_a_repr():
    """The default dataclass repr would put it in every traceback."""
    settings = Settings(nineveh_url="http://nas", token="nvh_supersecret")
    assert "nvh_supersecret" not in repr(settings)
    assert "redacted" in repr(settings)


def test_the_authorization_header_is_a_bearer_token():
    settings = Settings(nineveh_url="http://nas", token="nvh_abc")
    assert settings.authorization == {"Authorization": "Bearer nvh_abc"}


def test_a_world_readable_env_file_is_refused(tmp_path: Path):
    """A token any account on the machine can read is not a secret."""
    env = write_env(tmp_path, "NINEVEH_TOKEN=nvh_abc\n", mode=0o644)
    with pytest.raises(ConfigurationError) as raised:
        Settings.load(env)
    assert "chmod 600" in str(raised.value)


def test_an_absent_file_is_not_an_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NINEVEH_TOKEN", "nvh_abc")
    assert read_env_file(tmp_path / "nothing-here") == {}
    assert Settings.load(tmp_path / "nothing-here").token == "nvh_abc"


def test_comments_blanks_and_quotes_are_handled(tmp_path: Path):
    env = write_env(
        tmp_path,
        '# a comment\n\nNINEVEH_TOKEN="nvh_quoted"\nnot a pair\n  NINEVEH_URL = http://x \n',
    )
    values = read_env_file(env)
    assert values == {"NINEVEH_TOKEN": "nvh_quoted", "NINEVEH_URL": "http://x"}


def test_the_committed_example_ships_no_token():
    """The template must never carry a real credential."""
    example = Path(__file__).resolve().parent.parent / ".env.example"
    values = read_env_file(example)
    assert values.get("NINEVEH_TOKEN", "") == ""
