"""A closed registry of model-callable tools, none of which writes anything.

Besides the catalog reads, a filing-enabled Cleo may offer the user its
filing board. That tool touches no file and no service: it only names a
folder for a button the user may or may not press.
"""

from __future__ import annotations

from typing import Any

from cleo.domain import SEARCH_FIELD_LIMITS, ToolCall
from cleo.ports import CatalogGateway
from olympus.presentation import CONTROL_CHARACTERS


class ToolDispatchError(RuntimeError):
    """A model requested something outside Cleo's tool contract."""


FILING_TOOL = "open_filing_board"
MAX_FOLDER_LENGTH = 1_024
FILING_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FILING_TOOL,
        "description": (
            "Offer the user the filing board for a folder on their computer, "
            "where they review where each comic or manga volume would go and "
            "approve it. Use when they ask to file, upload, add, or place "
            "volumes from a folder. It uploads nothing by itself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "maxLength": MAX_FOLDER_LENGTH}
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
    },
}


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
    def __init__(self, catalog: CatalogGateway, filing: bool = False) -> None:
        self._catalog = catalog
        self._filing = filing

    @property
    def offers_filing(self) -> bool:
        return self._filing

    @property
    def definitions(self) -> tuple[dict[str, Any], ...]:
        if self._filing:
            return (*TOOL_DEFINITIONS, FILING_DEFINITION)
        return TOOL_DEFINITIONS

    async def execute(self, call: ToolCall) -> dict[str, Any]:
        if call.name == FILING_TOOL and self._filing:
            self._require_keys(call.arguments, {"folder"}, {"folder"})
            return {"folder": self._folder(call.arguments["folder"])}
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
    def _folder(value: Any) -> str:
        if not isinstance(value, str):
            raise ToolDispatchError("The folder must be text.")
        folder = CONTROL_CHARACTERS.sub("", value).strip().strip("`")
        if not folder or len(folder) > MAX_FOLDER_LENGTH:
            raise ToolDispatchError("The folder has an invalid length.")
        return folder

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
