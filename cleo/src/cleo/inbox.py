"""The only local files Cleo reads: `.cbz` volumes in a folder the user chose.

Trusted code, never a model tool. It lists rather than searches, skips hidden
entries, and never follows a symbolic link, so a link planted in a downloads
folder cannot lead it anywhere else.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import BinaryIO

from cleo.domain import FolderScan, LocalVolume
from olympus.presentation import human_size

EXTENSION = ".cbz"
MAX_VOLUME_BYTES = 4_000_000_000
MAX_VOLUMES = 1_000
# Bounds how much of a large tree one scan may walk.
MAX_FOLDERS = 2_000
MAX_COMICINFO_BYTES = 256_000
COMICINFO = "comicinfo.xml"
DIGITS = re.compile(r"(\d+)")
# What a damaged or unusual archive raises while its ComicInfo.xml is read.
ARCHIVE_FAILURES = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    RuntimeError,
    NotImplementedError,
    EOFError,
    ValueError,
)


class FolderInbox:
    def __init__(
        self,
        max_bytes: int = MAX_VOLUME_BYTES,
        max_volumes: int = MAX_VOLUMES,
        max_folders: int = MAX_FOLDERS,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_volumes = max_volumes
        self._max_folders = max_folders

    def scan(self, folder: Path, recursive: bool) -> FolderScan:
        """Every volume in `folder`, and its subfolders when `recursive`.

        A scan that hits a limit says so rather than passing for complete.
        Raises OSError when `folder` itself cannot be read.
        """
        found: list[LocalVolume] = []
        pending, walked = [folder], 0
        while (
            pending and walked < self._max_folders and len(found) <= self._max_volumes
        ):
            current = pending.pop(0)
            walked += 1
            for entry in self._entries(current, strict=current == folder):
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        pending.append(Path(entry.path))
                elif is_volume(entry):
                    found.append(self._volume(folder, entry))
        volumes = sorted(found, key=lambda item: natural_key(item.relative))
        return FolderScan(
            tuple(volumes[: self._max_volumes]), self._limit(len(found), pending)
        )

    def comic_info(self, volume: LocalVolume) -> str | None:
        """The archive's own ComicInfo.xml, when it has a sensible one."""
        try:
            with zipfile.ZipFile(volume.path) as archive:
                member = next(
                    (
                        info
                        for info in archive.infolist()
                        if info.filename.casefold() == COMICINFO
                    ),
                    None,
                )
                if member is None or member.file_size > MAX_COMICINFO_BYTES:
                    return None
                with archive.open(member) as handle:
                    data = handle.read(MAX_COMICINFO_BYTES + 1)
        except ARCHIVE_FAILURES:
            return None
        if len(data) > MAX_COMICINFO_BYTES:
            return None
        return data.decode("utf-8", errors="replace")

    def open(self, volume: LocalVolume) -> BinaryIO:
        """Open for upload, refusing a file swapped for a link since the scan."""
        descriptor = os.open(volume.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        return os.fdopen(descriptor, "rb")

    def _entries(self, folder: Path, strict: bool) -> list[os.DirEntry[str]]:
        try:
            with os.scandir(folder) as entries:
                return list(entries)
        except OSError:
            if strict:
                raise
            return []

    def _volume(self, folder: Path, entry: os.DirEntry[str]) -> LocalVolume:
        path = Path(entry.path)
        size = entry.stat(follow_symlinks=False).st_size
        return LocalVolume(
            path=path,
            relative=path.relative_to(folder).as_posix(),
            size=size,
            problem=self._problem(size),
        )

    def _limit(self, found: int, unread: list[Path]) -> str:
        if found > self._max_volumes:
            return f"Only the first {self._max_volumes:,} volumes are included."
        if unread:
            return f"Only the first {self._max_folders:,} folders were searched."
        return ""

    def _problem(self, size: int) -> str:
        if size == 0:
            return "The file is empty."
        if size > self._max_bytes:
            return f"Larger than the {human_size(self._max_bytes)} limit."
        return ""


def is_volume(entry: os.DirEntry[str]) -> bool:
    return entry.is_file(follow_symlinks=False) and entry.name.casefold().endswith(
        EXTENSION
    )


def natural_key(name: str) -> tuple[tuple[int, int | str], ...]:
    """`v2` before `v10`: digit runs compare as numbers."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in DIGITS.split(name)
        if part
    )
