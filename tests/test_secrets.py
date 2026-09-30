from __future__ import annotations

import json
from typing import Any, cast

import keyring.backends.null
import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from olympus import secrets
from olympus.ports import StoreError
from olympus.secrets import (
    FileSecretStore,
    KeychainSecretStore,
    PreferredSecretStore,
    environment_key,
)


def test_file_secrets_round_trip_privately_and_delete(tmp_path):
    path = tmp_path / "state" / "secrets.json"
    store = FileSecretStore(path)
    assert store.get("a", "token") is None
    store.set("a", "token", "secret")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    store.set("a", "other", "two")
    assert store.get("a", "token") == "secret"
    store.delete("a", "token")
    assert store.get("a", "token") is None
    store.delete_agent("a")
    assert json.loads(path.read_text()) == {}


def test_file_secrets_reject_invalid_and_symbolic_files(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]")
    with pytest.raises(StoreError, match="invalid"):
        FileSecretStore(invalid).get("a", "token")

    target = tmp_path / "target"
    target.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(StoreError, match="symbolic link"):
        FileSecretStore(link).set("a", "token", "secret")


def test_preferred_store_uses_environment_primary_and_fallback(monkeypatch, tmp_path):
    class Primary:
        def __init__(self):
            self.value = None

        def get(self, agent_id, key):
            return self.value

        def set(self, agent_id, key, value):
            if value == "fail":
                raise StoreError("no keychain")
            self.value = value

        def delete(self, agent_id, key):
            self.value = None

        def delete_agent(self, agent_id, keys=()):
            for key in keys:
                self.delete(agent_id, key)

    primary = Primary()
    fallback = FileSecretStore(tmp_path / "secrets.json")
    store = PreferredSecretStore(cast(Any, primary), fallback)
    assert store.location("agent-1", "token") is None
    store.set("agent-1", "token", "primary")
    assert store.get("agent-1", "token") == "primary"
    assert store.location("agent-1", "token") == "macOS Keychain"
    store.set("agent-1", "token", "fail")
    primary.value = None
    assert store.get("agent-1", "token") == "fail"
    assert store.location("agent-1", "token") == "private file"
    monkeypatch.setenv("OLYMPUS_SECRET_AGENT_1_TOKEN", "environment")
    assert store.get("agent-1", "token") == "environment"
    assert store.location("agent-1", "token") == "environment"
    store.delete_agent("agent-1", ("token",))
    assert fallback.get("agent-1", "token") is None


def test_keychain_adapter_uses_the_native_backend():
    class Backend:
        def __init__(self):
            self.value = "secret"
            self.calls = []

        def get_password(self, service, account):
            self.calls.append(("get", service, account))
            return self.value

        def set_password(self, service, account, value):
            self.calls.append(("set", service, account, value))
            self.value = value

        def delete_password(self, service, account):
            self.calls.append(("delete", service, account))
            self.value = None

    backend = Backend()
    store = KeychainSecretStore(backend)
    assert store.get("agent", "token") == "secret"
    store.set("agent", "token", "new")
    store.delete("agent", "token")
    assert [call[0] for call in backend.calls] == [
        "get",
        "set",
        "get",
        "delete",
        "get",
    ]
    assert backend.value is None


def test_keychain_failures_and_preferred_fallback_are_safe(tmp_path):
    class RefusedBackend:
        def get_password(self, *args):
            raise KeyringError("denied")

        def set_password(self, *args):
            raise KeyringError("denied")

        def delete_password(self, *args):
            raise KeyringError("denied")

    keychain = KeychainSecretStore(RefusedBackend())
    with pytest.raises(StoreError, match="unavailable"):
        keychain.get("agent", "token")
    with pytest.raises(StoreError, match="could not save"):
        keychain.set("agent", "token", "secret")
    with pytest.raises(StoreError, match="still holds token"):
        keychain.delete_agent("agent", ("token",))

    fallback = FileSecretStore(tmp_path / "secrets.json")
    fallback.set("agent", "token", "fallback")
    preferred = PreferredSecretStore(keychain, fallback)
    assert preferred.get("agent", "token") == "fallback"
    preferred.set("agent", "token", "kept in the file")
    assert fallback.get("agent", "token") == "kept in the file"
    with pytest.raises(StoreError, match="still holds"):
        preferred.delete("agent", "token")
    assert fallback.get("agent", "token") is None


def test_secret_store_factory_chooses_platform_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    assert secrets.build_secret_store(tmp_path)._primary is None
    monkeypatch.setattr(secrets.sys, "platform", "darwin")
    assert isinstance(
        secrets.build_secret_store(tmp_path)._primary, KeychainSecretStore
    )


def test_a_keychain_that_keeps_nothing_falls_back_to_the_file(tmp_path):
    fallback = FileSecretStore(tmp_path / "secrets.json")
    keychain = KeychainSecretStore(keyring.backends.null.Keyring())
    with pytest.raises(StoreError, match="did not keep"):
        keychain.set("agent", "token", "secret")
    preferred = PreferredSecretStore(keychain, fallback)
    preferred.set("agent", "token", "secret")
    assert preferred.get("agent", "token") == "secret"
    assert preferred.location("agent", "token") == "private file"


def test_an_absent_keychain_entry_counts_as_deleted():
    class Backend:
        def get_password(self, service, account):
            return None

        def delete_password(self, service, account):
            raise PasswordDeleteError("not found")

    KeychainSecretStore(Backend()).delete_agent("agent", ("token",))


def test_a_keychain_that_refuses_to_forget_is_reported():
    class Stubborn:
        def get_password(self, service, account):
            return "still here"

        def delete_password(self, service, account):
            raise PasswordDeleteError("could not delete")

    with pytest.raises(StoreError, match="still holds token"):
        KeychainSecretStore(Stubborn()).delete_agent("agent", ("token",))


def test_environment_override_names_are_predictable():
    assert environment_key("dev-cleo", "nineveh_token") == (
        "OLYMPUS_SECRET_DEV_CLEO_NINEVEH_TOKEN"
    )
