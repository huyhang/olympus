"""Process-level Olympus configuration and platform data locations."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "granite4.2:8b"


def default_state_dir() -> Path:
    configured = os.environ.get("OLYMPUS_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Olympus"
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local/share"
    return root / "olympus"


@dataclass(frozen=True, slots=True)
class Settings:
    state_dir: Path
    ollama_url_override: str = ""
    model_override: str = ""

    @classmethod
    def load(cls) -> Settings:
        return cls(
            state_dir=default_state_dir(),
            ollama_url_override=os.environ.get("OLLAMA_URL", "").rstrip("/"),
            model_override=os.environ.get("OLYMPUS_MODEL", ""),
        )
