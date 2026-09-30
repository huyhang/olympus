"""Stand-ins shared by the Olympus shell tests."""

from __future__ import annotations

import asyncio

from textual.widgets import Static

from olympus.app import OlympusApp
from olympus.config import Settings
from olympus.domain import (
    AgentDefinition,
    AgentEvent,
    AgentProfile,
    AgentRuntime,
    Candidate,
    ConfigurationField,
    Evidence,
)
from olympus.ports import ProviderError
from olympus.registry import AgentRegistry
from olympus.services import AgentService
from olympus.store import OlympusStore


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, agent_id, key):
        return self.values.get((agent_id, key))

    def set(self, agent_id, key, value):
        self.values[(agent_id, key)] = value

    def delete_agent(self, agent_id, keys=()):
        del keys
        self.values = {
            pair: value for pair, value in self.values.items() if pair[0] != agent_id
        }

    def location(self, agent_id, key):
        return "memory" if (agent_id, key) in self.values else None


class FakeSession:
    """Streams a canned answer with evidence, recording each question."""

    def __init__(self, label="answer"):
        self.label = label
        self.questions = []
        self.grounded = []

    async def respond(self, history, question, identity, prior_evidence=False):
        self.questions.append(question)
        self.grounded.append(prior_evidence)
        yield AgentEvent("status", f"{identity.name} is looking…")
        yield AgentEvent(
            "evidence", evidence=Evidence("memory", {"agent": identity.name})
        )
        yield AgentEvent("token", f"{self.label} ")
        yield AgentEvent("token", "complete")
        yield AgentEvent("done", f"{self.label} complete")


class HangingSession(FakeSession):
    """Streams part of an answer, then holds until the test releases it."""

    def __init__(self, label="slow"):
        super().__init__(label)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, history, question, identity, prior_evidence=False):
        self.questions.append(question)
        yield AgentEvent("status", f"{identity.name} is reading…")
        yield AgentEvent(
            "evidence", evidence=Evidence("slow_lookup", {"agent": identity.name})
        )
        yield AgentEvent("token", f"{self.label} partial")
        self.started.set()
        await self.release.wait()
        yield AgentEvent("done", f"{self.label} finished")


class ChoosingSession(FakeSession):
    """Asks the user to pick a series first, then answers the pick."""

    async def respond(self, history, question, identity, prior_evidence=False):
        self.questions.append(question)
        if len(self.questions) == 1:
            yield AgentEvent(
                "choice",
                text="Which one?",
                candidates=(Candidate("one", "One"), Candidate("two", "Two")),
            )
        else:
            yield AgentEvent("done", "Selected")


class FakeProvider:
    definition = AgentDefinition(
        "fake",
        "Oracle",
        "A deterministic test agent",
        "O",
        (ConfigurationField("token", "Token", secret=True),),
    )

    def __init__(self):
        self.sessions = {}
        self.opened = []
        self.closed = []
        self.probe_gate: asyncio.Event | None = None
        self.refuse_open = False

    def validate(self, profile, secrets):
        return {} if secrets.get("token") else {"token": "Token required"}

    async def probe(self, profile, secrets, defaults):
        if self.probe_gate is not None:
            await self.probe_gate.wait()

    def open(self, profile, secrets, defaults):
        if self.refuse_open:
            raise ProviderError("The fake agent cannot start.")
        session = self.sessions.setdefault(profile.id, FakeSession(profile.name))
        model = profile.model or defaults.model
        self.opened.append((profile.id, model))

        async def close():
            self.closed.append((profile.id, model))

        return AgentRuntime(session, model, self.definition.description, (close,))


def configured(tmp_path, count=1, settings=None):
    store = OlympusStore(tmp_path / "state")
    provider = FakeProvider()
    service = AgentService(store, AgentRegistry((provider,)), MemorySecrets())
    profiles = []
    for number in range(count):
        draft = service.draft("fake")
        profile = AgentProfile(
            id=draft.id,
            kind=draft.kind,
            name=f"Oracle {number + 1}",
            model=f"model-{number + 1}",
        )
        profiles.append(service.save(profile, {"token": f"token-{number}"}))
    app = OlympusApp(service, settings or Settings(tmp_path / "state"))
    return app, service, provider, profiles


def transcript(app) -> list[str]:
    """The workspace transcript as displayed, oldest first."""
    main = app.screen_stack[0]
    return [widget.source for widget in main.query("#transcript > Markdown")]


def transcript_text(app) -> str:
    return "\n".join(transcript(app))


def evidence_titles(app) -> list[str]:
    main = app.screen_stack[0]
    return [str(panel.title) for panel in main.query("#transcript > Collapsible")]


def status_line(app) -> str:
    return str(app.screen_stack[0].query_one("#status", Static).render())


def identity_line(app) -> str:
    return str(app.screen_stack[0].query_one("#identity", Static).render())


async def started(session: HangingSession, seconds: float = 5) -> None:
    """Wait for a hanging reply to begin, failing fast if it never does."""
    await asyncio.wait_for(session.started.wait(), seconds)


async def finish_replies(app, pilot) -> None:
    """Wait for every reply in flight, then for the shell to collect them."""
    tasks = [reply.task for reply in app._replies.values() if reply.task]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await pilot.pause()
    await pilot.pause()


async def type_and_send(pilot, text: str) -> None:
    app = pilot.app
    composer = app.screen_stack[0].query_one("#composer")
    composer.focus()
    composer.text = text
    await pilot.press("enter")
    await pilot.pause()


def notifications(app) -> list[str]:
    return [notification.message for notification in app._notifications]
