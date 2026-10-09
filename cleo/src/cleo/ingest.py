"""Nineveh's ingest routes, for a Cleo whose owner turned filing on.

Separate from the read-only catalog adapter, which still has no write path,
and only ever constructed for an agent with filing enabled. Staging writes
nothing to the library; committing places a staged volume. Which of those a
token may do is Nineveh's decision, not Cleo's.
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from cleo.config import CleoSettings
from cleo.nineveh import MAX_RESPONSE_BYTES, reason
from cleo.ports import IngestError, Progress

INGEST = "/api/v1/librarian/ingest"
MAX_SERIES_ID = 64
MAX_FILENAME = 255
MAX_INGEST_ID = 200
# Uploads are large and Nineveh validates the archive before answering.
UPLOAD_TIMEOUT = httpx.Timeout(10.0, read=120.0, write=600.0)
FAILURES = {
    401: "Nineveh rejected Cleo's credential.",
    403: "Nineveh refused: this token may not do that.",
    404: "Nineveh no longer has that upload.",
    409: "Nineveh reported a conflict with the library.",
    413: "The volume is too large for Nineveh.",
    422: "Nineveh could not accept that volume.",
}


class ProgressReader:
    """A file that reports how much of itself has been read, for uploads."""

    def __init__(self, stream: BinaryIO, size: int, report: Progress) -> None:
        self._stream = stream
        self._size = max(size, 1)
        self._report = report

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self._report(min(self._stream.tell() / self._size, 1.0))
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def fileno(self) -> int:
        return self._stream.fileno()


class NinevehIngestClient:
    def __init__(
        self,
        settings: CleoSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = settings.nineveh_url
        self._authorization = settings.authorization
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def pending(self) -> dict[str, Any]:
        """What waits for a commit. Doubles as a check of `ingest:stage`."""
        return await self._send("GET", INGEST)

    async def stage(
        self,
        series_id: str,
        filename: str,
        content: BinaryIO,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        _check(series_id, MAX_SERIES_ID, "series identifier")
        _check(filename, MAX_FILENAME, "filename")
        upload = _with_progress(content, progress)
        return await self._send(
            "POST",
            INGEST,
            data={"series_id": series_id, "filename": filename},
            files={"file": (filename, upload, "application/octet-stream")},
            timeout=UPLOAD_TIMEOUT,
        )

    async def commit(
        self, ingest_id: str, filename: str | None = None
    ) -> dict[str, Any]:
        if filename is not None:
            _check(filename, MAX_FILENAME, "filename")
        return await self._send(
            "POST", f"{_upload_path(ingest_id)}/commit", json={"filename": filename}
        )

    async def discard(self, ingest_id: str) -> None:
        await self._send("DELETE", _upload_path(ingest_id))

    async def _send(self, method: str, path: str, **options: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._authorization,
                **options,
            )
        except httpx.ConnectError as error:
            raise IngestError("Nineveh is unavailable.") from error
        except httpx.TimeoutException as error:
            raise IngestError("Nineveh did not respond in time.") from error
        except httpx.HTTPError as error:
            raise IngestError("Nineveh returned an invalid response.") from error
        if response.status_code >= 400:
            message = FAILURES.get(
                response.status_code, f"Nineveh returned HTTP {response.status_code}."
            )
            raise IngestError(
                f"{message}{await reason(response)}", response.status_code
            )
        return _payload(response)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _payload(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise IngestError("Nineveh's response was unexpectedly large.")
    try:
        value = json.loads(response.content)
    except ValueError as error:
        raise IngestError("Nineveh returned an invalid response.") from error
    if not isinstance(value, dict):
        raise IngestError("Nineveh returned an invalid response.")
    return value


def _check(value: str, limit: int, label: str) -> None:
    if not value or len(value) > limit:
        raise IngestError(f"Nineveh requires a valid {label}.")


def _upload_path(ingest_id: str) -> str:
    """An upload's route. The ID came over the network, so it stays one segment."""
    _check(ingest_id, MAX_INGEST_ID, "upload identifier")
    return f"{INGEST}/{quote(ingest_id, safe='')}"


def _with_progress(
    content: BinaryIO, progress: Progress | None
) -> BinaryIO | ProgressReader:
    if progress is None:
        return content
    size = content.seek(0, 2)
    content.seek(0)
    return ProgressReader(content, size, progress)
