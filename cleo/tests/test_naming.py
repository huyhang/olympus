from __future__ import annotations

import pytest
from cleo.domain import VolumeHint
from cleo.naming import hint_from_comicinfo, hint_from_filename


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("vinland_saga_v12.cbz", VolumeHint("vinland saga", "12", "filename")),
        (
            "[Digital] Vinland.Saga.v14 (2024) (1r0n).cbz",
            VolumeHint("Vinland Saga", "14", "filename"),
        ),
        ("Pluto 001.cbz", VolumeHint("Pluto", "1", "filename")),
        ("Saga #12.CBZ", VolumeHint("Saga", "12", "filename")),
        (
            "20th Century Boys Vol. 3.cbz",
            VolumeHint("20th Century Boys", "3", "filename"),
        ),
        ("Monster - Volume 07.cbz", VolumeHint("Monster", "7", "filename")),
        ("Gantz {HQ} tome 5.cbz", VolumeHint("Gantz", "5", "filename")),
        ("mystery_scan.cbz", VolumeHint("mystery scan", None, "filename")),
        ("Vinland Saga.cbz", VolumeHint("Vinland Saga", None, "filename")),
        ("v01.cbz", None),
        ("[Group only].cbz", None),
        (f"{'x' * 201}.cbz", None),
        ("\x1b[31mRed\x1b[0m v2.cbz", VolumeHint("Red", "2", "filename")),
        ("vinland-saga-v09.cbz", VolumeHint("vinland saga", "9", "filename")),
        ("[Group] x-men-v02.cbz", VolumeHint("x men", "2", "filename")),
        ("Spider-Man v01.cbz", VolumeHint("Spider-Man", "1", "filename")),
        ("Spider-Man.cbz", VolumeHint("Spider Man", None, "filename")),
    ],
)
def test_a_filename_names_its_series_and_volume(filename, expected):
    assert hint_from_filename(filename) == expected


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            "<ComicInfo><Series>Pluto</Series><Number>008</Number></ComicInfo>",
            VolumeHint("Pluto", "8", "comicinfo"),
        ),
        (
            "<ComicInfo><Series> Vinland  Saga </Series><Volume>12</Volume></ComicInfo>",
            VolumeHint("Vinland Saga", "12", "comicinfo"),
        ),
        (
            "<ComicInfo><Series>Gantz</Series><Number>7.5</Number></ComicInfo>",
            VolumeHint("Gantz", "7.5", "comicinfo"),
        ),
        (
            "<ComicInfo><Series>Saga</Series><Number>one</Number></ComicInfo>",
            VolumeHint("Saga", None, "comicinfo"),
        ),
        ("<ComicInfo><Number>1</Number></ComicInfo>", None),
        (f"<ComicInfo><Series>{'x' * 201}</Series></ComicInfo>", None),
        ("<ComicInfo><Series>unclosed", None),
        ("not xml at all", None),
    ],
)
def test_comicinfo_names_the_series_when_it_can(document, expected):
    assert hint_from_comicinfo(document) == expected
