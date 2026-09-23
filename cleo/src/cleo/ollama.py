"""Streaming Ollama chat adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from cleo.domain import ModelChunk, ToolCall
from cleo.ports import ModelError


class OllamaChatModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None

    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[ModelChunk]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": True,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": 1_024},
        }
        if tools:
            body["tools"] = list(tools)
        try:
            async with self._client.stream("POST", self._url, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    yield self._parse_chunk(line)
        except httpx.ConnectError as error:
            raise ModelError(
                "Ollama is unavailable. Start it and confirm granite4.2:8b is installed."
            ) from error
        except httpx.TimeoutException as error:
            raise ModelError("Ollama did not respond in time.") from error
        except httpx.HTTPStatusError as error:
            raise ModelError(
                f"Ollama returned HTTP {error.response.status_code}."
            ) from error
        except httpx.HTTPError as error:
            raise ModelError("The connection to Ollama failed.") from error

    @staticmethod
    def _parse_chunk(line: str) -> ModelChunk:
        try:
            value = json.loads(line)
            if value.get("error"):
                raise ModelError(f"Ollama: {value['error']}")
            message = value.get("message") or {}
            calls = tuple(
                OllamaChatModel._parse_call(item)
                for item in message.get("tool_calls") or []
            )
            return ModelChunk(
                content=str(message.get("content") or ""), tool_calls=calls
            )
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            raise ModelError(
                "Ollama returned an invalid streaming response."
            ) from error

    @staticmethod
    def _parse_call(value: dict[str, Any]) -> ToolCall:
        function = value.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        return ToolCall(
            name=str(function["name"]),
            arguments=arguments,
            call_id=str(value.get("id") or ""),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
