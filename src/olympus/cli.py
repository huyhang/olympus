"""Olympus command-line entry point and composition root."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from cleo.provider import CleoProvider

from olympus import __version__
from olympus.app import OlympusApp
from olympus.config import Settings
from olympus.domain import AgentProfile
from olympus.ports import OlympusError
from olympus.registry import AgentRegistry
from olympus.secrets import build_secret_store
from olympus.services import AgentService
from olympus.store import OlympusStore

# The development harness's throwaway Cleo; see `_bootstrap_fake_agent`.
BOOTSTRAP_AGENT_ID = "dev-cleo"


def build_app(settings: Settings | None = None) -> OlympusApp:
    configured = settings or Settings.load()
    store = OlympusStore(configured.state_dir)
    try:
        secrets = build_secret_store(configured.state_dir)
        registry = AgentRegistry.discover((CleoProvider(),))
        service = AgentService(store, registry, secrets)
        _bootstrap_fake_agent(service)
    except BaseException:
        store.close()
        raise
    return OlympusApp(service, configured)


def _bootstrap_fake_agent(service: AgentService) -> None:
    """Seed only the explicit development harness; normal first run stays empty.

    The seeded agent has a fixed id so the harness can hand it a token through
    its per-agent override variable instead of any persistent store.
    """
    if os.environ.get("OLYMPUS_BOOTSTRAP_CLEO") != "1" or service.store.agents():
        return
    profile = service.draft("cleo")
    service.save(
        AgentProfile(
            id=BOOTSTRAP_AGENT_ID,
            kind=profile.kind,
            name=profile.name,
            settings={
                "nineveh_url": os.environ.get("NINEVEH_URL", "http://localhost:8080")
            },
        ),
        {},
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="olympus", description="One terminal home for local Ollama agents."
    )
    result.add_argument("--version", action="version", version=f"Olympus {__version__}")
    result.add_argument(
        "--data-dir", action="store_true", help="print the persistent data directory"
    )
    return result


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parser().parse_args(argv)
    settings = Settings.load()
    if arguments.data_dir:
        print(settings.state_dir)
        return
    try:
        app = build_app(settings)
    except (OlympusError, OSError) as error:
        raise SystemExit(f"olympus: {error}") from error
    app.run()
