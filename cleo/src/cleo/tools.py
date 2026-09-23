"""A closed registry of model-callable, read-only catalog tools."""

from __future__ import annotations

from typing import Any

from cleo.domain import SEARCH_FIELD_LIMITS, ToolCall
from cleo.ports import CatalogGateway


class ToolDispatchError(RuntimeError):
    """A model requested something outside Cleo's tool contract."""


TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "list_libraries",
            "description": "List the Nineveh libraries this credential can read.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_series",
            "description": (
                "Search Nineveh for a series by its title or local folder name. "
                "Do not use this tool to search for an author."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 200},
                    "library": {"type": "string", "maxLength": 200},
                    "artist": {"type": "string", "maxLength": 200},
                    "publisher": {"type": "string", "maxLength": 200},
                    "status": {"type": "string", "maxLength": 64},
                    "tag": {"type": "string", "maxLength": 64},
                    "title": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_series_by_author",
            "description": (
                "Find series by an author's full name or partial name such as a "
                "surname. Always use this for questions asking which series an "
                "author wrote."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "author": {"type": "string", "maxLength": 200},
                    "library": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["author"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_series",
            "description": (
                "Get Nineveh's full catalog details for one exact series ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {"series_id": {"type": "string", "maxLength": 200}},
                "required": ["series_id"],
                "additionalProperties": False,
            },
        },
    },
)


class ReadOnlyToolRegistry:
    def __init__(self, catalog: CatalogGateway) -> None:
        self._catalog = catalog

    @property
    def definitions(self) -> tuple[dict[str, Any], ...]:
        return TOOL_DEFINITIONS

    async def execute(self, call: ToolCall) -> dict[str, Any]:
        if call.name == "list_libraries":
            self._require_keys(call.arguments, set())
            return await self._catalog.list_libraries()
        if call.name == "search_series":
            # Everything the catalog accepts except `author`, which gets its
            # own tool because small models confuse the two.
            allowed = (set(SEARCH_FIELD_LIMITS) - {"author"}) | {"limit"}
            self._require_keys(call.arguments, allowed)
            self._validate_search(call.arguments)
            return await self._catalog.search_series(**call.arguments)
        if call.name == "find_series_by_author":
            self._require_keys(
                call.arguments, {"author", "library", "limit"}, {"author"}
            )
            self._validate_search(call.arguments)
            return await self._catalog.search_series(**call.arguments)
        if call.name == "get_series":
            self._require_keys(call.arguments, {"series_id"}, {"series_id"})
            series_id = call.arguments["series_id"]
            if not isinstance(series_id, str) or not series_id or len(series_id) > 200:
                raise ToolDispatchError("Series ID must be valid text.")
            return await self._catalog.get_series(series_id)
        raise ToolDispatchError(f"Unsupported tool: {call.name}")

    @staticmethod
    def _require_keys(
        arguments: dict[str, Any], allowed: set[str], required: set[str] | None = None
    ) -> None:
        unknown = set(arguments) - allowed
        missing = (required or set()) - set(arguments)
        if unknown or missing:
            raise ToolDispatchError("The tool arguments do not match its schema.")

    @staticmethod
    def _validate_search(arguments: dict[str, Any]) -> None:
        limit = arguments.get("limit")
        if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 50):
            raise ToolDispatchError("Search limit must be between 1 and 50.")
        for key, value in arguments.items():
            if key == "limit":
                continue
            if not isinstance(value, str):
                raise ToolDispatchError(f"Search field {key} must be text.")
            if not value or len(value) > SEARCH_FIELD_LIMITS[key]:
                raise ToolDispatchError(f"Search field {key} has an invalid length.")
