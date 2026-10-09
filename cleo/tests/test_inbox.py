from __future__ import annotations

import os
import zipfile

import pytest
from cleo.domain import LocalVolume
from cleo.inbox import FolderInbox, natural_key


def volume(path, pages=1, comicinfo=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for number in range(pages):
            archive.writestr(f"{number:03}.jpg", b"page")
        if comicinfo is not None:
            archive.writestr("ComicInfo.xml", comicinfo)
    return path


@pytest.fixture
def downloads(tmp_path):
    folder = tmp_path / "downloads"
    volume(folder / "Pluto v10.cbz")
    volume(folder / "Pluto v2.cbz")
    volume(folder / "LOUD.CBZ")
    volume(folder / "nested" / "Saga v1.cbz")
    volume(folder / ".hidden.cbz")
    (folder / "notes.txt").write_text("not a volume")
    (folder / "empty.cbz").write_bytes(b"")
    outside = volume(tmp_path / "elsewhere" / "Secret v1.cbz")
    (folder / "linked.cbz").symlink_to(outside)
    (folder / "linked-folder").symlink_to(outside.parent, target_is_directory=True)
    return folder


def names(scan):
    return [item.relative for item in scan.volumes]


def test_a_scan_lists_volumes_naturally_and_skips_links_and_hidden(downloads):
    found = FolderInbox().scan(downloads, recursive=False)
    assert names(found) == ["empty.cbz", "LOUD.CBZ", "Pluto v2.cbz", "Pluto v10.cbz"]
    assert found.volumes[0].problem == "The file is empty."
    assert all(not item.problem for item in found.volumes[1:])
    assert found.limit == ""


def test_a_recursive_scan_walks_real_subfolders_only(downloads):
    found = FolderInbox().scan(downloads, recursive=True)
    assert "nested/Saga v1.cbz" in names(found)
    assert not any("Secret" in name for name in names(found))


@pytest.mark.parametrize(
    ("limits", "recursive", "count", "limit"),
    [
        ({"max_volumes": 2}, True, 2, "Only the first 2 volumes are included."),
        ({"max_volumes": 4}, False, 4, ""),
        ({"max_volumes": 3}, False, 3, "Only the first 3 volumes are included."),
        ({"max_folders": 1}, True, 4, "Only the first 1 folders were searched."),
        ({"max_folders": 2}, True, 5, ""),
        ({"max_folders": 1}, False, 4, ""),
    ],
)
def test_a_scan_that_hits_a_limit_says_so(downloads, limits, recursive, count, limit):
    found = FolderInbox(**limits).scan(downloads, recursive=recursive)
    assert (len(found.volumes), found.limit) == (count, limit)


@pytest.mark.parametrize(
    ("max_bytes", "problem"),
    [
        (10, "Larger than the 10 B limit."),
        (100, "Larger than the 100 B limit."),
        (4_000_000_000, ""),
    ],
)
def test_a_volume_over_the_size_limit_names_the_limit(downloads, max_bytes, problem):
    found = FolderInbox(max_bytes=max_bytes).scan(downloads, recursive=False)
    assert found.volumes[-1].problem == problem


def test_an_unreadable_folder_raises_but_a_vanished_subfolder_does_not(tmp_path):
    with pytest.raises(OSError):
        FolderInbox().scan(tmp_path / "missing", recursive=False)
    inbox = FolderInbox()
    assert inbox._entries(tmp_path / "missing", strict=False) == []


@pytest.mark.parametrize(
    ("comicinfo", "expected"),
    [
        ("<ComicInfo><Series>Pluto</Series></ComicInfo>", "Pluto"),
        (None, None),
        ("x" * 300_000, None),
    ],
)
def test_comic_info_is_read_only_when_present_and_small(tmp_path, comicinfo, expected):
    path = volume(tmp_path / "v.cbz", comicinfo=comicinfo)
    found = FolderInbox().comic_info(LocalVolume(path, "v.cbz", path.stat().st_size))
    assert (found is not None and expected in found) or (found is expected is None)


def test_a_damaged_archive_has_no_comic_info(tmp_path):
    path = tmp_path / "broken.cbz"
    path.write_bytes(b"PK not really a zip")
    assert FolderInbox().comic_info(LocalVolume(path, "broken.cbz", 19)) is None


def test_open_reads_the_volume_but_refuses_a_swapped_in_link(tmp_path):
    real = volume(tmp_path / "real.cbz")
    inbox = FolderInbox()
    with inbox.open(LocalVolume(real, "real.cbz", 1)) as handle:
        assert handle.read(2) == b"PK"
    link = tmp_path / "link.cbz"
    link.symlink_to(real)
    if getattr(os, "O_NOFOLLOW", 0):
        with pytest.raises(OSError):
            inbox.open(LocalVolume(link, "link.cbz", 1))


@pytest.mark.parametrize(
    ("names_in", "expected"),
    [
        (["v10", "v2", "v1"], ["v1", "v2", "v10"]),
        (["b", "A", "c"], ["A", "b", "c"]),
        (["Saga 3", "Pluto 1"], ["Pluto 1", "Saga 3"]),
    ],
)
def test_natural_order_compares_numbers_as_numbers(names_in, expected):
    assert sorted(names_in, key=natural_key) == expected
