"""Stand-ins shared by the Olympus shell tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace

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
    Proposal,
    SuggestedAction,
)
from olympus.ports import ProviderError, ReviewError
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


class OfferingSession(FakeSession):
    """Answers every question by offering to review a folder."""

    def __init__(self, folder):
        super().__init__("offer")
        self.folder = folder

    async def respond(self, history, question, identity, prior_evidence=False):
        self.questions.append(question)
        yield AgentEvent(
            "action",
            text=f"I can review {self.folder}.",
            action=SuggestedAction("review_folder", "Open the board", str(self.folder)),
        )


class ScriptedReviewSession:
    """Shows fixed proposals, then applies each decision as the agent would."""

    def __init__(self, proposals, listener, fail_run=None, notice=""):
        self.proposals = {item.id: item for item in proposals}
        self.listener = listener
        self.fail_run = fail_run
        self.notice = notice
        self.actions = []
        self.closed = False

    async def run(self):
        for proposal in self.proposals.values():
            self.listener(proposal)
        if self.fail_run is not None:
            raise self.fail_run
        return self.notice

    async def place(self, proposal_id):
        self.actions.append(("place", proposal_id))
        self._set(
            proposal_id,
            state="placed",
            evidence=(Evidence("place", {"placed": proposal_id}),),
        )

    async def skip(self, proposal_id):
        self.actions.append(("skip", proposal_id))
        self._set(proposal_id, state="skipped")

    async def rename(self, proposal_id, filename):
        self.actions.append(("rename", proposal_id, filename))
        if filename == "bad":
            raise ReviewError("That name will not do.")
        self._set(proposal_id, filename=filename)

    async def choose(self, proposal_id, choice):
        self.actions.append(("choose", proposal_id, choice.id))
        self._set(proposal_id, state="ready", subject=choice, destination="Lib/X")

    async def search(self, query):
        self.actions.append(("search", query))
        if query == "down":
            raise ReviewError("Search failed.")
        return (Candidate("found", f"{query} found"),)

    async def close(self):
        self.closed = True

    def _set(self, proposal_id, **changes):
        self.proposals[proposal_id] = replace(self.proposals[proposal_id], **changes)
        self.listener(self.proposals[proposal_id])


class ScriptedWorkflow:
    title = "File things"

    def __init__(self, proposals=(), fail_run=None, notice=""):
        self.proposals = tuple(proposals)
        self.fail_run = fail_run
        self.notice = notice
        self.sessions = []
        self.described = []

    def describe(self, folder, recursive):
        self.described.append((folder, recursive))
        return f"{len(self.proposals)} things here"

    def open(self, folder, recursive, listener):
        session = ScriptedReviewSession(
            self.proposals, listener, self.fail_run, self.notice
        )
        self.sessions.append((folder, recursive, session))
        return session


def sample_proposals():
    return (
        Proposal(
            "a.cbz",
            "a.cbz",
            "ready",
            size=2_000_000,
            pages=10,
            subject=Candidate("s1", "Series One"),
            subject_note="Manga · title match 0.97",
            destination="Manga/Series One",
            filename="Series One 001.cbz",
            suggested_filename="Series One 001.cbz",
        ),
        Proposal(
            "b.cbz",
            "b.cbz",
            "ready",
            subject=Candidate("s1", "Series One"),
            destination="Manga/Series One",
            filename="Series One 002.cbz",
            warning="Already in the library",
        ),
        Proposal(
            "c.cbz",
            "c.cbz",
            "needs_choice",
            alternatives=(Candidate("s1", "Series One"), Candidate("s2", "Two")),
        ),
    )


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
        self.workflow = None

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

        return AgentRuntime(
            session, model, self.definition.description, (close,), self.workflow
        )


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
    app = OlympusApp(
        service, settings or Settings(tmp_path / "state"), start_folder=tmp_path
    )
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
