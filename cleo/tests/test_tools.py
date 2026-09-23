from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cleo.domain import ToolCall
from cleo.tools import ReadOnlyToolRegistry, ToolDispatchError


class FakeCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def list_libraries(self):
        self.calls.append(("list", None))
        return {"libraries": []}

    async def search_series(self, **filters):
        self.calls.append(("search", filters))
        return {"series": []}

    async def get_series(self, series_id):
        self.calls.append(("detail", series_id))
        return {"id": series_id}


def run(awaitable):
    return asyncio.run(awaitable)


def test_only_four_read_tools_are_exposed():
    registry = ReadOnlyToolRegistry(FakeCatalog())
    names = {item["function"]["name"] for item in registry.definitions}
    assert names == {
        "list_libraries",
        "search_series",
        "find_series_by_author",
        "get_series",
    }
    assert not names & {"delete", "discard", "commit", "ingest", "shell", "http"}


def test_each_registered_tool_calls_the_typed_catalog_port():
    catalog = FakeCatalog()
    registry = ReadOnlyToolRegistry(catalog)
    run(registry.execute(ToolCall("list_libraries", {})))
    run(registry.execute(ToolCall("search_series", {"query": "Pluto", "limit": 5})))
    run(
        registry.execute(
            ToolCall("find_series_by_author", {"author": "Urasawa", "limit": 5})
        )
    )
    result = run(registry.execute(ToolCall("get_series", {"series_id": "pluto"})))
    assert result == {"id": "pluto"}
    assert catalog.calls == [
        ("list", None),
        ("search", {"query": "Pluto", "limit": 5}),
        ("search", {"author": "Urasawa", "limit": 5}),
        ("detail", "pluto"),
    ]


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("discard_ingest", {"ingest_id": "1"}),
        ToolCall("list_libraries", {"url": "https://example.com"}),
        ToolCall("search_series", {"limit": 51}),
        ToolCall("search_series", {"query": "x" * 201}),
        ToolCall("search_series", {"author": "Urasawa"}),
        ToolCall("find_series_by_author", {}),
        ToolCall("get_series", {}),
        ToolCall("get_series", {"series_id": 123}),
        ToolCall("search_series", {"query": 7}),
        ToolCall("search_series", {"query": ""}),
        ToolCall("search_series", {"limit": "five"}),
    ],
)
def test_unknown_tools_and_invalid_arguments_never_reach_the_catalog(call):
    catalog = FakeCatalog()
    with pytest.raises(ToolDispatchError):
        run(ReadOnlyToolRegistry(catalog).execute(call))
    assert catalog.calls == []
