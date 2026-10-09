from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from olympus.domain import Candidate, Evidence, Proposal
from olympus.ui import proposals as view
from olympus.ui.folders import complete_folder, resolve_folder

READY = Proposal(
    "a",
    "inbox/a.cbz",
    "ready",
    size=182_000_000,
    pages=214,
    subject=Candidate("s", "Vinland Saga"),
    subject_note="Manga · title match 0.97",
    destination="Manga/Vinland Saga",
    filename="Vinland Saga 012.cbz",
    suggested_filename="Vinland Saga 012.cbz",
    pattern="Vinland Saga NNN.cbz",
)
WARNED = Proposal("b", "b.cbz", "ready", warning="Already in the library")


def rendered(renderable) -> str:
    console = Console(width=100, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


@pytest.mark.parametrize(
    ("proposal", "mark"),
    [
        (Proposal("x", "x", "pending"), "◌"),
        (Proposal("x", "x", "working"), "◐"),
        (Proposal("x", "x", "needs_choice"), "?"),
        (READY, "●"),
        (WARNED, "⚠"),
        (Proposal("x", "x", "placed"), "✓"),
        (Proposal("x", "x", "held"), "⏸"),
        (Proposal("x", "x", "skipped"), "–"),
        (Proposal("x", "x", "failed"), "✗"),
    ],
)
def test_each_state_has_its_glyph(proposal, mark):
    assert view.glyph(proposal)[0] == mark


def test_the_tally_counts_in_display_order_and_skips_zeroes():
    proposals = [
        READY,
        WARNED,
        Proposal("c", "c", "pending"),
        Proposal("d", "d", "placing"),
        Proposal("e", "e", "placed"),
        Proposal("f", "f", "needs_choice"),
    ]
    assert view.tally(proposals) == [
        ("placed", 1),
        ("ready", 1),
        ("to check", 1),
        ("need you", 1),
        ("in progress", 2),
    ]
    assert view.tally_text(proposals).plain == (
        "6 files   1 placed   1 ready   1 to check   1 need you   2 in progress"
    )
    assert view.tally_text([READY]).plain == "1 file   1 ready"


@pytest.mark.parametrize(
    ("proposal", "row"),
    [
        (READY, "● inbox/a.cbz"),
        (
            Proposal("x", "x.cbz", "working", activity="Uploading 40%"),
            "◐ x.cbz  Uploading 40%",
        ),
        (Proposal("x", "x.cbz", "needs_choice"), "? x.cbz  choose a series"),
        (
            Proposal("x", "\x1b[31mx.cbz", "failed", activity="Refused"),
            "✗ x.cbz  Refused",
        ),
    ],
)
def test_a_row_names_the_file_and_what_is_happening(proposal, row):
    assert view.row_text(proposal).plain == row


@pytest.mark.parametrize(
    ("state", "retryable", "action", "expected"),
    [
        ("ready", True, "place", True),
        ("ready", True, "rename", True),
        ("needs_choice", True, "place", False),
        ("needs_choice", True, "choose", True),
        ("failed", True, "choose", True),
        ("failed", False, "choose", False),
        ("failed", False, "skip", True),
        ("pending", True, "skip", True),
        ("working", True, "skip", False),
        ("placed", True, "skip", False),
    ],
)
def test_actions_follow_the_state(state, retryable, action, expected):
    proposal = Proposal("x", "x", state, retryable=retryable)
    assert view.allowed(proposal, action) is expected


def test_no_action_applies_to_nothing():
    assert not view.allowed(None, "place")


def test_place_all_skips_warnings_and_unready_volumes():
    assert view.placeable([READY, WARNED, Proposal("c", "c", "placed")]) == ["a"]
    assert view.unsettled([READY, Proposal("c", "c", "placed")]) == 1


@pytest.mark.parametrize(
    ("proposal", "rows"),
    [
        (
            READY,
            [
                ("Series", "Vinland Saga"),
                ("", "Manga · title match 0.97"),
                ("Lands in", "Manga/Vinland Saga/"),
                ("Renamed", "a.cbz"),
                ("", "→ Vinland Saga 012.cbz"),
                ("Follows", "Vinland Saga NNN.cbz"),
            ],
        ),
        (
            Proposal(
                "p", "Pluto 008.cbz", "ready", destination="/", filename="Pluto 008.cbz"
            ),
            [("Lands in", "/"), ("Filename", "Pluto 008.cbz")],
        ),
        (
            Proposal(
                "c",
                "c.cbz",
                "needs_choice",
                activity="Several could match.",
                alternatives=(Candidate("1", "Saga"), Candidate("2", "Vinland Saga")),
            ),
            [
                ("Series", "Not sure yet — press c to choose"),
                ("Matches", "Saga, Vinland Saga"),
                ("Why", "Several could match."),
            ],
        ),
        (
            Proposal(
                "d",
                "d.cbz",
                "placed",
                subject=Candidate("s", "Pluto"),
                destination="Manga/Pluto",
                filename="Pluto 009.cbz",
            ),
            [("Series", "Pluto"), ("Placed at", "Manga/Pluto/Pluto 009.cbz")],
        ),
        (
            Proposal("e", "e.cbz", "held", activity="Waiting for approval."),
            [("Status", "Waiting for approval.")],
        ),
        (
            Proposal(
                "f", "f.cbz", "ready", warning="Duplicate", activity="Not placed: x"
            ),
            [("Check", "Duplicate"), ("Status", "Not placed: x")],
        ),
        (Proposal("g", "g.cbz", "working"), []),
    ],
)
def test_the_detail_pane_describes_the_proposal(proposal, rows):
    assert [(row.label, row.value) for row in view.detail_rows(proposal)] == rows


def test_the_detail_pane_renders_heading_facts_and_rows():
    text = rendered(view.detail_renderable(READY))
    assert "inbox/a.cbz" in text and "182.0 MB · 214 pages" in text
    assert "Lands in  Manga/Vinland Saga/" in text
    assert "Looking through" in rendered(view.detail_renderable(None))


def test_the_summary_records_every_outcome():
    proposals = [
        Proposal("a", "a.cbz", "placed", destination="Manga/X", filename="X 001.cbz"),
        Proposal("b", "b.cbz", "held"),
        Proposal("c", "c.cbz", "failed", activity="Refused"),
        Proposal("d", "d.cbz", "failed"),
        Proposal("e", "e.cbz", "skipped"),
        Proposal("f", "f.cbz", "ready"),
    ]
    summary = view.summary_markdown("File things", Path("/in"), proposals)
    assert summary.splitlines() == [
        "**File things** · `/in`",
        "",
        "Placed 1 of 6 files.",
        "",
        "- ✓ `a.cbz` → `Manga/X/X 001.cbz`",
        "- ⏸ `b.cbz` — uploaded, awaiting approval",
        "- ✗ `c.cbz` — Refused",
        "- ✗ `d.cbz` — failed",
        "- – `e.cbz` — skipped",
        "- – `f.cbz` — left undecided; nothing was placed",
    ]
    single = view.summary_markdown("T", Path("/in"), proposals[:1])
    assert "Placed 1 of 1 file." in single


def test_summary_evidence_is_one_panel_or_none():
    placed = Proposal("a", "a.cbz", "placed", evidence=(Evidence("place", {"id": 1}),))
    [record] = view.summary_evidence(Path("/in"), [placed, READY])
    assert record.tool == "folder_review"
    assert record.arguments == {"folder": "/in"}
    assert record.payload == [
        {"source": "a.cbz", "tool": "place", "response": {"id": 1}}
    ]
    assert view.summary_evidence(Path("/in"), [READY]) == ()


CHILDREN = {"/home/me": ["Downloads", "Documents", ".cache"], "/home/me/Downloads": []}


@pytest.mark.parametrize(
    ("typed", "completed"),
    [
        ("/home/me/Dow", "/home/me/Downloads/"),
        ("/home/me/Do", "/home/me/Documents/"),
        ("/home/me/", "/home/me/Documents/"),
        ("/home/me/.c", "/home/me/.cache/"),
        ("/home/me/Downloads", None),
        ("/home/me/Zzz", None),
        ("", None),
    ],
)
def test_folder_completion_extends_the_last_segment(typed, completed):
    assert (
        complete_folder(typed, lambda parent: CHILDREN.get(str(parent), []))
        == completed
    )


def test_resolve_folder_accepts_only_existing_folders(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    assert resolve_folder(f" {tmp_path} ") == tmp_path.resolve()
    assert resolve_folder(str(tmp_path / "file.txt")) is None
    assert resolve_folder("   ") is None
