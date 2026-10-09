"""How review proposals are summarised, labelled, and described.

Pure functions over `Proposal` snapshots, so the review board stays a thin
view. Every proposal field is agent text and passes through `plain_text`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Group
from rich.table import Table
from rich.text import Text

from olympus.domain import Evidence, Proposal
from olympus.presentation import human_size, plain_text

GLYPHS: dict[str, tuple[str, str]] = {
    "pending": ("◌", "#8b9bab"),
    "working": ("◐", "#79c0ff"),
    "needs_choice": ("?", "#d2a8ff"),
    "ready": ("●", "#3fb950"),
    "placing": ("◐", "#3fb950"),
    "placed": ("✓", "#3fb950"),
    "held": ("⏸", "#d29922"),
    "skipped": ("–", "#6e7681"),
    "failed": ("✗", "#f85149"),
}
WARNING_GLYPH = ("⚠", "#d29922")
# Each state's tally label, in display order. Ready proposals with a warning
# are counted apart, under WARNING_LABEL.
TALLY_LABELS: tuple[tuple[str, str], ...] = (
    ("placed", "placed"),
    ("ready", "ready"),
    ("warning", "to check"),
    ("needs_choice", "need you"),
    ("held", "awaiting approval"),
    ("working", "in progress"),
    ("failed", "failed"),
    ("skipped", "skipped"),
)
IN_PROGRESS = frozenset({"pending", "working", "placing"})
ACTIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"skip"}),
    "needs_choice": frozenset({"skip", "choose"}),
    "ready": frozenset({"place", "skip", "choose", "rename"}),
    "failed": frozenset({"skip", "choose"}),
}


@dataclass(frozen=True, slots=True)
class DetailRow:
    label: str
    value: str
    style: str = ""


def glyph(proposal: Proposal) -> tuple[str, str]:
    if proposal.state == "ready" and proposal.warning:
        return WARNING_GLYPH
    return GLYPHS[proposal.state]


def tally_key(proposal: Proposal) -> str:
    if proposal.state == "ready" and proposal.warning:
        return "warning"
    if proposal.state in IN_PROGRESS:
        return "working"
    return proposal.state


def tally(proposals: Iterable[Proposal]) -> list[tuple[str, int]]:
    """Non-zero counts by tally label, in display order."""
    counts: dict[str, int] = {}
    for proposal in proposals:
        key = tally_key(proposal)
        counts[key] = counts.get(key, 0) + 1
    return [(label, counts[key]) for key, label in TALLY_LABELS if counts.get(key)]


def tally_text(proposals: Sequence[Proposal]) -> Text:
    total = len(proposals)
    text = Text(f"{total} file{'' if total == 1 else 's'}", style="bold")
    styles = {label: _tally_style(key) for key, label in TALLY_LABELS}
    for label, count in tally(proposals):
        text.append("   ")
        text.append(f"{count} {label}", style=styles[label])
    return text


def _tally_style(key: str) -> str:
    if key == "warning":
        return WARNING_GLYPH[1]
    return GLYPHS["working" if key == "working" else key][1]


def row_text(proposal: Proposal) -> Text:
    """One file-list line: glyph, file, and what is happening to it."""
    mark, style = glyph(proposal)
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{mark} ", style=f"bold {style}")
    text.append(plain_text(proposal.source))
    note = row_note(proposal)
    if note:
        text.append(f"  {note}", style="#8b9bab")
    return text


def row_note(proposal: Proposal) -> str:
    if proposal.state in {"working", "placing", "held", "failed"}:
        return plain_text(proposal.activity)
    if proposal.state == "needs_choice":
        return "choose a series"
    return ""


def allowed(proposal: Proposal | None, action: str) -> bool:
    if proposal is None or (action == "choose" and not proposal.retryable):
        return False
    return action in ACTIONS.get(proposal.state, frozenset())


def placeable(proposals: Iterable[Proposal]) -> list[str]:
    """What "place all" may place: ready, and nothing to check first."""
    return [item.id for item in proposals if item.state == "ready" and not item.warning]


def unsettled(proposals: Iterable[Proposal]) -> int:
    return sum(1 for item in proposals if not item.settled)


def facts(proposal: Proposal) -> str:
    parts = [human_size(proposal.size)] if proposal.size else []
    if proposal.pages:
        parts.append(f"{proposal.pages} pages")
    return " · ".join(parts)


def detail_rows(proposal: Proposal) -> list[DetailRow]:
    """The labelled lines of the detail pane, top to bottom."""
    rows = _subject_rows(proposal)
    if proposal.state == "placed":
        rows.append(DetailRow("Placed at", _landing(proposal), "bold #3fb950"))
    elif proposal.destination:
        rows.append(DetailRow("Lands in", _folder(proposal.destination)))
        rows += _filename_rows(proposal)
    if proposal.warning:
        rows.append(DetailRow("Check", plain_text(proposal.warning), "#d29922"))
    rows += _activity_rows(proposal)
    return rows


def _subject_rows(proposal: Proposal) -> list[DetailRow]:
    if proposal.subject is None:
        if proposal.state != "needs_choice":
            return []
        return [DetailRow("Series", "Not sure yet — press c to choose", "#d2a8ff")]
    rows = [DetailRow("Series", plain_text(proposal.subject.title), "bold")]
    if proposal.subject_note:
        rows.append(DetailRow("", plain_text(proposal.subject_note), "#8b9bab"))
    return rows


def _filename_rows(proposal: Proposal) -> list[DetailRow]:
    if not proposal.filename:
        return []
    name = plain_text(proposal.filename)
    if not proposal.renamed:
        return [DetailRow("Filename", name)]
    original = plain_text(proposal.source.rsplit("/", 1)[-1])
    rows = [DetailRow("Renamed", original, "#8b9bab"), DetailRow("", f"→ {name}")]
    if proposal.pattern:
        rows.append(DetailRow("Follows", plain_text(proposal.pattern), "#8b9bab"))
    return rows


def _activity_rows(proposal: Proposal) -> list[DetailRow]:
    activity = plain_text(proposal.activity)
    if proposal.state == "needs_choice":
        found = ", ".join(plain_text(item.title) for item in proposal.alternatives[:3])
        rows = [DetailRow("Matches", found)] if found else []
        return rows + ([DetailRow("Why", activity, "#8b9bab")] if activity else [])
    if not activity:
        return []
    style = {"failed": "#f85149", "held": "#d29922"}.get(proposal.state, "#79c0ff")
    if proposal.state == "ready":
        style = "#f85149"
    return [DetailRow("Status", activity, style)]


def _folder(path: str) -> str:
    cleaned = plain_text(path).rstrip("/")
    return f"{cleaned}/" if cleaned else "/"


def _landing(proposal: Proposal) -> str:
    return f"{_folder(proposal.destination)}{plain_text(proposal.filename)}"


def detail_renderable(proposal: Proposal | None) -> Group | Text:
    if proposal is None:
        return Text("Looking through the folder…", style="#8b9bab")
    heading = Text(plain_text(proposal.source), style="bold #ffffff")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="#8b9bab", no_wrap=True)
    grid.add_column(overflow="fold")
    for row in detail_rows(proposal):
        grid.add_row(row.label, Text(row.value, style=row.style))
    return Group(heading, Text(facts(proposal), style="#8b9bab"), Text(""), grid)


def summary_markdown(title: str, folder: Path, proposals: Sequence[Proposal]) -> str:
    """The record the review leaves in the conversation."""
    placed = sum(1 for item in proposals if item.state == "placed")
    lines = [
        f"**{plain_text(title)}** · `{plain_text(str(folder))}`",
        "",
        f"Placed {placed} of {len(proposals)} file{'' if len(proposals) == 1 else 's'}.",
        "",
    ]
    lines += [f"- {_summary_line(item)}" for item in proposals]
    return "\n".join(lines)


def _summary_line(proposal: Proposal) -> str:
    source = f"`{plain_text(proposal.source)}`"
    if proposal.state == "placed":
        return f"✓ {source} → `{_landing(proposal)}`"
    if proposal.state == "held":
        return f"⏸ {source} — uploaded, awaiting approval"
    if proposal.state == "failed":
        return f"✗ {source} — {plain_text(proposal.activity) or 'failed'}"
    if proposal.state == "skipped":
        return f"– {source} — skipped"
    return f"– {source} — left undecided; nothing was placed"


def summary_evidence(
    folder: Path, proposals: Iterable[Proposal]
) -> tuple[Evidence, ...]:
    """Every record the agent kept, as one panel rather than one per file."""
    records = [
        {"source": proposal.source, "tool": item.tool, "response": item.payload}
        for proposal in proposals
        for item in proposal.evidence
    ]
    if not records:
        return ()
    return (Evidence("folder_review", records, arguments={"folder": str(folder)}),)
