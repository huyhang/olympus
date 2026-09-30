"""Credential storage that prefers macOS Keychain and safely falls back."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from olympus.ports import StoreError

KEYCHAIN_SERVICE = "dev.olympus.agents"


def _account(agent_id: str, key: str) -> str:
    return f"{agent_id}:{key}"


class KeychainSecretStore:
    """Adapter over keyring's native macOS Keychain backend."""

    def __init__(self, backend: object = keyring) -> None:
        self._backend = backend

    def get(self, agent_id: str, key: str) -> str | None:
        try:
            return self._backend.get_password(KEYCHAIN_SERVICE, _account(agent_id, key))
        except KeyringError as error:
            raise StoreError("macOS Keychain is unavailable.") from error

    def set(self, agent_id: str, key: str, value: str) -> None:
        try:
            self._backend.set_password(KEYCHAIN_SERVICE, _account(agent_id, key), value)
        except KeyringError as error:
            raise StoreError("macOS Keychain could not save the credential.") from error
        if self.get(agent_id, key) != value:
            # keyring's null and fail backends accept a write and keep nothing.
            raise StoreError("macOS Keychain did not keep the credential.")

    def delete_agent(self, agent_id: str, keys: Sequence[str] = ()) -> None:
        kept = [key for key in keys if not self._deleted(agent_id, key)]
        if kept:
            raise StoreError(
                f"macOS Keychain still holds {', '.join(kept)} for this agent. "
                f"Remove it in Keychain Access under {KEYCHAIN_SERVICE}."
            )

    def delete(self, agent_id: str, key: str) -> None:
        self.delete_agent(agent_id, (key,))

    def _deleted(self, agent_id: str, key: str) -> bool:
        """Delete one entry and confirm it is gone. An absent entry counts."""
        try:
            self._backend.delete_password(KEYCHAIN_SERVICE, _account(agent_id, key))
        except PasswordDeleteError:
            return self._absent(agent_id, key)  # keyring's way of saying "absent"
        except KeyringError:
            return False
        return self._absent(agent_id, key)

    def _absent(self, agent_id: str, key: str) -> bool:
        try:
            return self.get(agent_id, key) is None
        except StoreError:
            return False


class FileSecretStore:
    """Owner-only JSON fallback for systems without a working Keychain."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, agent_id: str, key: str) -> str | None:
        return self._read().get(agent_id, {}).get(key)

    def set(self, agent_id: str, key: str, value: str) -> None:
        values = self._read()
        values.setdefault(agent_id, {})[key] = value
        self._write(values)

    def delete_agent(self, agent_id: str, keys: Sequence[str] = ()) -> None:
        del keys
        values = self._read()
        if values.pop(agent_id, None) is not None:
            self._write(values)

    def delete(self, agent_id: str, key: str) -> None:
        values = self._read()
        agent = values.get(agent_id, {})
        if agent.pop(key, None) is None:
            return
        if not agent:
            values.pop(agent_id, None)
        self._write(values)

    def _read(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        if self._path.is_symlink():
            raise StoreError("Olympus's secrets file cannot be a symbolic link.")
        self._path.chmod(0o600)
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StoreError("Olympus's secrets file is unreadable.") from error
        if not isinstance(value, dict):
            raise StoreError("Olympus's secrets file is invalid.")
        return value

    def _write(self, values: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        if self._path.exists() and self._path.is_symlink():
            raise StoreError("Olympus's secrets file cannot be a symbolic link.")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".secrets-", dir=self._path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(values, output, ensure_ascii=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
            self._path.chmod(0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)


class PreferredSecretStore:
    """Use Keychain when it works, retaining a portable secure fallback.

    Falling back is by design, never silent: `location` reports where each
    credential actually lives, and a failed Keychain deletion is raised.
    """

    def __init__(self, primary: KeychainSecretStore | None, fallback: FileSecretStore):
        self._primary = primary
        self._fallback = fallback

    def get(self, agent_id: str, key: str) -> str | None:
        environment = os.environ.get(environment_key(agent_id, key))
        if environment:
            return environment
        return self._primary_value(agent_id, key) or self._fallback.get(agent_id, key)

    def set(self, agent_id: str, key: str, value: str) -> None:
        if self._stored_in_primary(agent_id, key, value):
            self._fallback.delete(agent_id, key)
        else:
            self._fallback.set(agent_id, key, value)

    def delete_agent(self, agent_id: str, keys: Sequence[str] = ()) -> None:
        self._fallback.delete_agent(agent_id, keys)
        if self._primary:
            self._primary.delete_agent(agent_id, keys)

    def delete(self, agent_id: str, key: str) -> None:
        self._fallback.delete(agent_id, key)
        if self._primary:
            self._primary.delete(agent_id, key)

    def location(self, agent_id: str, key: str) -> str | None:
        if os.environ.get(environment_key(agent_id, key)):
            return "environment"
        if self._primary_value(agent_id, key):
            return "macOS Keychain"
        if self._fallback.get(agent_id, key):
            return "private file"
        return None

    def _primary_value(self, agent_id: str, key: str) -> str | None:
        if self._primary is None:
            return None
        try:
            return self._primary.get(agent_id, key)
        except StoreError:
            return None  # an unavailable Keychain defers to the private file

    def _stored_in_primary(self, agent_id: str, key: str, value: str) -> bool:
        if self._primary is None:
            return False
        try:
            self._primary.set(agent_id, key, value)
        except StoreError:
            return False  # kept in the private file instead; `location` says so
        return True


def environment_key(agent_id: str, key: str) -> str:
    """The variable that overrides one agent's stored credential."""
    normalized = re.sub(r"[^A-Za-z0-9]", "_", f"{agent_id}_{key}").upper()
    return f"OLYMPUS_SECRET_{normalized}"


def build_secret_store(state_dir: Path) -> PreferredSecretStore:
    keychain = KeychainSecretStore() if sys.platform == "darwin" else None
    return PreferredSecretStore(keychain, FileSecretStore(state_dir / "secrets.json"))
