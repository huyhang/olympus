"""The deliberately read-only Nineveh adapter."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from cleo.config import Settings
from cleo.domain import SEARCH_FIELD_LIMITS
from cleo.ports import CatalogError

MAX_RESPONSE_BYTES = 256_000


class NinevehCatalogClient:
    """Expose only the three read operations Cleo needs.

    There is intentionally no general request method in the public interface
    and no ingest path anywhere in this adapter.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = settings.nineveh_url
        self._authorization = settings.authorization
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def list_libraries(self) -> dict[str, Any]:
        return await self._get("/api/v1/librarian/libraries")

    async def search_series(self, **filters: Any) -> dict[str, Any]:
        allowed = set(SEARCH_FIELD_LIMITS) | {"limit"}
        if set(filters) - allowed:
            raise CatalogError("Unsupported Nineveh series filter.")
        params = {
            key: value
            for key, value in filters.items()
            if key in allowed and value is not None and value != ""
        }
        return await self._get("/api/v1/librarian/series", params=params)

    async def get_series(self, series_id: str) -> dict[str, Any]:
        if not series_id or len(series_id) > 200:
            raise CatalogError("Nineveh requires a valid series identifier.")
        encoded_id = quote(series_id, safe="")
        return await self._get(f"/api/v1/librarian/series/{encoded_id}")

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}{path}",
                params=params,
                headers=self._authorization,
            ) as response:
                # Handled here rather than after the stream closes, so Nineveh's
                # own explanation of the refusal is still readable.
                if response.status_code >= 400:
                    raise CatalogError(await describe_failure(response))
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise CatalogError("Nineveh's response was unexpectedly large.")
                value = json.loads(content)
        except httpx.ConnectError as error:
            raise CatalogError("Nineveh is unavailable.") from error
        except httpx.TimeoutException as error:
            raise CatalogError("Nineveh did not respond in time.") from error
        except (httpx.HTTPError, ValueError) as error:
            raise CatalogError("Nineveh returned an invalid response.") from error
        if not isinstance(value, dict):
            raise CatalogError("Nineveh returned an invalid response.")
        return value

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def describe_failure(response: httpx.Response) -> str:
    status = response.status_code
    message = {
        401: "Nineveh rejected Cleo's credential.",
        403: "Nineveh denied access to that catalog data.",
        404: "Nineveh could not find that series.",
    }.get(status, f"Nineveh returned HTTP {status}.")
    return f"{message}{await reason(response)}"


async def reason(response: httpx.Response) -> str:
    """Nineveh's own account of the refusal, which is usually the actionable part."""
    try:
        body = json.loads(await response.aread())
    except (httpx.HTTPError, ValueError):
        return ""
    if not isinstance(body, dict):
        return ""
    detail = body.get("detail") or body.get("Detail")
    if not detail:
        return ""
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    return f" Nineveh said: {text}"
