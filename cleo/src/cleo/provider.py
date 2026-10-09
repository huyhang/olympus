"""Cleo's plug-in boundary for the Olympus application shell."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from cleo.agent import Librarian
from cleo.config import CleoSettings
from cleo.filing import FilingServices, FilingWorkflow
from cleo.inbox import FolderInbox
from cleo.ingest import NinevehIngestClient
from cleo.matcher import ModelTitleGuesser, SeriesMatcher
from cleo.nineveh import NinevehCatalogClient
from cleo.ollama import OllamaChatModel
from cleo.ports import CatalogError, IngestError
from cleo.tools import ReadOnlyToolRegistry
from olympus.domain import (
    TOGGLE_OFF,
    TOGGLE_ON,
    AgentDefinition,
    AgentProfile,
    AgentRuntime,
    ConfigurationField,
    OllamaDefaults,
)
from olympus.ports import ProviderError, SecretStore
from olympus.validation import http_url_problem


class CleoProvider:
    @property
    def definition(self) -> AgentDefinition:
        return AgentDefinition(
            kind="cleo",
            name="Cleo",
            description="Librarian for a Nineveh catalog",
            glyph="C",
            fields=(
                ConfigurationField(
                    "nineveh_url",
                    "Nineveh URL",
                    placeholder="http://localhost:8080",
                    default="http://localhost:8080",
                ),
                ConfigurationField(
                    "nineveh_token",
                    "Nineveh token",
                    placeholder="nvh_…",
                    secret=True,
                ),
                ConfigurationField(
                    "filing",
                    "Allow filing volumes from a folder",
                    placeholder=(
                        "You approve every volume. Needs a token with ingest:stage, "
                        "and ingest:commit to place volumes from here."
                    ),
                    default=TOGGLE_OFF,
                    required=False,
                    kind="toggle",
                ),
            ),
        )

    def validate(
        self, profile: AgentProfile, secrets: Mapping[str, str]
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        problem = http_url_problem(
            profile.settings.get("nineveh_url", ""), "Nineveh URL"
        )
        if problem:
            errors["nineveh_url"] = problem
        if not secrets.get("nineveh_token"):
            errors["nineveh_token"] = "Enter a Nineveh librarian token."
        return errors

    async def probe(
        self,
        profile: AgentProfile,
        secrets: Mapping[str, str],
        defaults: OllamaDefaults,
    ) -> None:
        settings = self._settings(profile, secrets, defaults)
        catalog = NinevehCatalogClient(settings)
        try:
            await catalog.list_libraries()
        except CatalogError as error:
            raise ProviderError(str(error)) from error
        finally:
            await catalog.aclose()
        if filing_enabled(profile):
            await self._probe_filing(settings)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{settings.ollama_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
                models = {
                    str(item.get("name"))
                    for item in payload.get("models", [])
                    if isinstance(item, dict) and item.get("name")
                }
                if settings.model not in models:
                    raise ProviderError(
                        f"Ollama is reachable, but {settings.model} is not installed."
                    )
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            raise ProviderError("Ollama is unavailable at that URL.") from error

    @staticmethod
    async def _probe_filing(settings: CleoSettings) -> None:
        ingest = NinevehIngestClient(settings)
        try:
            await ingest.pending()
        except IngestError as error:
            if error.status == 403:
                raise ProviderError(
                    "Filing is on, but this token cannot upload volumes "
                    "(it needs ingest:stage)."
                ) from error
            raise ProviderError(f"Filing is on, but {error}") from error
        finally:
            await ingest.aclose()

    def open(
        self,
        profile: AgentProfile,
        secrets: SecretStore,
        defaults: OllamaDefaults,
    ) -> AgentRuntime:
        supplied = {"nineveh_token": secrets.get(profile.id, "nineveh_token") or ""}
        errors = self.validate(profile, supplied)
        if errors:
            raise ProviderError(next(iter(errors.values())))
        settings = self._settings(profile, supplied, defaults)
        catalog = NinevehCatalogClient(settings)
        model = OllamaChatModel(settings.ollama_url, settings.model)
        filing = filing_enabled(profile)
        session = Librarian(model, ReadOnlyToolRegistry(catalog, filing=filing))
        runtime = AgentRuntime(
            session=session,
            model=settings.model,
            summary=self.definition.description,
            close_async=(model.aclose, catalog.aclose),
        )
        return (
            self._with_filing(runtime, settings, catalog, model) if filing else runtime
        )

    @staticmethod
    def _with_filing(
        runtime: AgentRuntime,
        settings: CleoSettings,
        catalog: NinevehCatalogClient,
        model: OllamaChatModel,
    ) -> AgentRuntime:
        """The same runtime, plus the filing board and the ingest client it uses.

        The workflow closes first, while the clients it withdraws uploads
        through are still open.
        """
        ingest = NinevehIngestClient(settings)
        inbox = FolderInbox()
        finder = SeriesMatcher(catalog, inbox, ModelTitleGuesser(model))
        workflow = FilingWorkflow(FilingServices(inbox, finder, ingest))
        return AgentRuntime(
            session=runtime.session,
            model=runtime.model,
            summary=f"{runtime.summary} · filing on",
            close_async=(workflow.aclose, *runtime.close_async, ingest.aclose),
            workflow=workflow,
        )

    @staticmethod
    def _settings(
        profile: AgentProfile,
        secrets: Mapping[str, str],
        defaults: OllamaDefaults,
    ) -> CleoSettings:
        return CleoSettings(
            nineveh_url=profile.settings["nineveh_url"].rstrip("/"),
            token=secrets.get("nineveh_token", ""),
            ollama_url=(profile.ollama_url or defaults.url).rstrip("/"),
            model=profile.model or defaults.model,
        )


def filing_enabled(profile: AgentProfile) -> bool:
    return profile.settings.get("filing") == TOGGLE_ON
