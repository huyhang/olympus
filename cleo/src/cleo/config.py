"""Runtime connection values supplied to Cleo by Olympus."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CleoSettings:
    nineveh_url: str
    token: str
    ollama_url: str = "http://localhost:11434"
    model: str = "granite4.2:8b"

    def __repr__(self) -> str:
        return (
            f"CleoSettings(nineveh_url={self.nineveh_url!r}, token=<redacted>, "
            f"ollama_url={self.ollama_url!r}, model={self.model!r})"
        )

    @property
    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}
