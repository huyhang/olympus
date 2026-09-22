"""Where Cleo finds Nineveh, and the credential it presents.

The token is a bearer secret that Nineveh shows exactly once at creation and
stores only as a hash. It lives in an untracked `.env` beside this project
rather than in the repository or on a command line, where it would land in
shell history and process listings.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
URL_KEY = "NINEVEH_URL"
TOKEN_KEY = "NINEVEH_TOKEN"
DEFAULT_URL = "http://localhost:8080"


class ConfigurationError(RuntimeError):
    """Something is missing or unsafe about the local configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    nineveh_url: str
    token: str

    def __repr__(self) -> str:
        """Never render the secret.

        The default dataclass repr would put the token into any traceback,
        log line, or debugger frame that happens to touch this object.
        """
        return f"Settings(nineveh_url={self.nineveh_url!r}, token=<redacted>)"

    @property
    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """Read `.env`, letting real environment variables win.

        That precedence is what lets a container or a CI job supply the token
        without a file, while a laptop keeps using one.
        """
        path = env_file if env_file is not None else ENV_FILE
        values = read_env_file(path)
        url = os.environ.get(URL_KEY) or values.get(URL_KEY) or DEFAULT_URL
        token = os.environ.get(TOKEN_KEY) or values.get(TOKEN_KEY) or ""
        # Only a file that actually holds the secret needs locking down. The
        # committed `.env.example` is world-readable on purpose.
        if values.get(TOKEN_KEY):
            require_private(path)
        if not token:
            raise ConfigurationError(
                f"No {TOKEN_KEY}. Copy .env.example to .env and paste the token "
                "from Nineveh's Admin -> Librarian page, or export it."
            )
        return cls(nineveh_url=url.rstrip("/"), token=token)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse `KEY=value` lines. Absent file is not an error."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def require_private(path: Path) -> None:
    """A token any account on the machine can read is not much of a secret.

    Applied only to a file that actually carries one -- the committed
    `.env.example` is world-readable by design.
    """
    if path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
        raise ConfigurationError(
            f"{path} holds a token and is readable by other accounts. "
            f"Run: chmod 600 {path}"
        )
