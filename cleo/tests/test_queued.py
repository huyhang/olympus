from __future__ import annotations

import hashlib
import io

import pytest
from cleo.queued import (
    Fingerprint,
    find,
    queued_note,
    queued_subject,
    records,
    sha256_of,
)

from olympus.domain import Candidate
from olympus.presentation import format_timestamp

DATA = b"PK" + b"page" * 1000
DIGEST = hashlib.sha256(DATA).hexdigest()


def test_a_fingerprint_hashes_what_is_read_and_starts_over_on_a_rewind():
    fingerprint = Fingerprint(io.BytesIO(DATA))
    assert fingerprint.seek(0, 2) == len(DATA)
    fingerprint.seek(0)
    fingerprint.read(100)
    fingerprint.seek(0)
    while fingerprint.read(512):
        pass
    assert (fingerprint.hexdigest(), fingerprint.length) == (DIGEST, len(DATA))
    assert fingerprint.tell() == len(DATA)


def test_a_fingerprint_offers_the_file_descriptor_of_a_real_file(tmp_path):
    path = tmp_path / "v.cbz"
    path.write_bytes(DATA)
    with path.open("rb") as stream:
        assert Fingerprint(stream).fileno() == stream.fileno()
        assert sha256_of(stream) == DIGEST


@pytest.mark.parametrize(
    ("payload", "ids"),
    [
        ({"pending": [{"ingestId": "a"}, {"ingestId": "b"}]}, ["a", "b"]),
        ({"pending": [{"ingestId": ""}, "junk", {"filename": "x"}]}, []),
        ({"pending": "nothing"}, []),
        ([], []),
    ],
)
def test_only_well_formed_uploads_are_read_from_the_queue(payload, ids):
    assert [item["ingestId"] for item in records(payload)] == ids


QUEUE = [
    {"ingestId": "a", "sha256": DIGEST, "size": 10, "seriesId": "pluto"},
    {"ingestId": "b", "sha256": DIGEST, "size": len(DATA), "seriesId": "saga"},
    {"ingestId": "c", "sha256": DIGEST, "size": len(DATA), "seriesId": "pluto"},
]


@pytest.mark.parametrize(
    ("series_id", "taken", "expected"),
    [
        (None, frozenset(), "b"),
        ("pluto", frozenset(), "c"),
        (None, frozenset({"b"}), "c"),
        ("saga", frozenset({"b"}), None),
        ("berserk", frozenset(), None),
    ],
)
def test_an_upload_is_found_by_its_content(series_id, taken, expected):
    found = find(QUEUE, DIGEST, len(DATA), series_id, taken)
    assert (found["ingestId"] if found else None) == expected


CREATED = "2026-10-08T21:30:00+00:00"


@pytest.mark.parametrize(
    ("record", "subject", "note"),
    [
        (
            {
                "seriesId": "pluto",
                "targetPath": "Manga/Pluto/Pluto 010.cbz",
                "createdAt": CREATED,
            },
            Candidate("pluto", "Pluto"),
            f"Manga · uploaded {format_timestamp(CREATED)} · waiting in Nineveh's queue",
        ),
        (
            {"seriesId": "pluto"},
            Candidate("pluto", "pluto"),
            "uploaded earlier · waiting in Nineveh's queue",
        ),
    ],
)
def test_a_queued_upload_says_where_it_waits(record, subject, note):
    assert (queued_subject(record), queued_note(record)) == (subject, note)
