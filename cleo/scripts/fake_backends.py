#!/usr/bin/env python3
"""Scripted stand-ins for Ollama and Nineveh, for exercising Olympus.

    .venv/bin/python cleo/scripts/fake_backends.py --launch # fakes and Olympus
    .venv/bin/python cleo/scripts/fake_backends.py [scenario] # fakes only

One local port answers both Ollama's streaming `/api/chat` and Nineveh's
read-only `/api/v1/librarian/*`. Both are needed: Cleo replaces any answer
that no successful catalog read backs, so a fake model on its own only ever
produces the refusal.

The model is scripted, not simulated. Each rule in the scenario lists keywords
to look for in the latest user message and the steps to play for it; the Nth
request since that message plays step N. Nothing is remembered between
requests, so a conversation can be restarted or rephrased freely. The catalog
is a real, if tiny, one: searches are answered by filtering it.

A scenario is one JSON file (see `scenarios/demo.json`):

- `libraries`, `series`: the catalog. A series gives `volumes` as a count and
  may carry an `error` (`status`, `detail`) that its detail read returns.
- `rules`: tried in order, first match wins. `match` is a keyword or a list of
  them, compared case-insensitively. Put specific rules before general ones.
- `fallback`: the steps for a message no rule matches.

A step is one of: `{"tool": name, "arguments": {...}}` to call a tool;
`{"reply": text, "delay": seconds}` to stream an answer word by word;
`{"error": text}` for an Ollama error mid-stream; `{"status": code}` for an
HTTP failure; `{"raw": text}` for a line that is not JSON at all.

Standard library only, so it runs from any Python and never ships in the wheel.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIO = HERE / "scenarios" / "demo.json"
# The per-agent credential override for the Cleo that `OLYMPUS_BOOTSTRAP_CLEO`
# seeds (olympus.secrets.environment_key for agent "dev-cleo"). The fake token
# is handed over this way so it never reaches Keychain or any other store.
FAKE_TOKEN_VARIABLE = "OLYMPUS_SECRET_DEV_CLEO_NINEVEH_TOKEN"
# Beside Ollama's 11434, so a real one can keep running.
DEFAULT_PORT = 11435
DEFAULT_DELAY = 0.04
LIBRARIAN = "/api/v1/librarian"
METADATA_FILTERS = ("author", "artist", "publisher", "status", "tag", "title")
VOLUME_BYTES = 180_000_000
VOLUME_PAGES = 200
WORDS = re.compile(r"\S+\s*")


class Scenario:
    """The catalog the fake Nineveh serves and the script the fake model plays."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.libraries: list[dict[str, Any]] = document.get("libraries", [])
        self.series = {entry["seriesId"]: entry for entry in document.get("series", [])}
        self.rules: list[dict[str, Any]] = document.get("rules", [])
        self.fallback: list[dict[str, Any]] = document.get(
            "fallback", [{"reply": "(fake Ollama: no rule matched that message.)"}]
        )

    @classmethod
    def load(cls, path: Path) -> Scenario:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    # --- the model ------------------------------------------------------------

    def next_step(self, messages: list[dict[str, Any]]) -> tuple[str, int, dict]:
        """The latest user message picks the rule; rounds since it pick the step."""
        users = [i for i, item in enumerate(messages) if item.get("role") == "user"]
        question = messages[users[-1]].get("content", "") if users else ""
        since = messages[users[-1] + 1 :] if users else messages
        round_ = sum(1 for item in since if item.get("role") == "assistant")
        name, steps = self._rule_for(str(question))
        if round_ >= len(steps):
            return (
                name,
                round_,
                {"reply": f"(fake Ollama: rule {name!r} has no step {round_ + 1}.)"},
            )
        return name, round_, steps[round_]

    def _rule_for(self, question: str) -> tuple[str, list[dict[str, Any]]]:
        text = question.casefold()
        for rule in self.rules:
            keywords = (
                rule["match"] if isinstance(rule["match"], list) else [rule["match"]]
            )
            if any(keyword.casefold() in text for keyword in keywords):
                return keywords[0], rule["steps"]
        return "fallback", self.fallback

    # --- the catalog ----------------------------------------------------------

    def search(self, params: dict[str, str]) -> dict[str, Any]:
        """Title resolution when there is a query, a metadata filter otherwise."""
        entries = [e for e in self.series.values() if self._in_library(e, params)]
        limit = int(params.get("limit") or 20)
        query = params.get("query", "").casefold()
        if query:
            matches = [m for m in (self._title_match(e, query) for e in entries) if m]
            matches.sort(key=lambda match: -match["score"])
            return {
                "candidates": matches[:limit],
                "confidentMatch": matches[0]["seriesId"] if len(matches) == 1 else None,
                "ambiguous": len(matches) > 1,
            }
        filters = {k: v.casefold() for k, v in params.items() if k in METADATA_FILTERS}
        found = [
            self._summary(entry)
            for entry in entries
            if all(self._has(entry, key, value) for key, value in filters.items())
        ]
        return {"candidates": found[:limit], "confidentMatch": None, "ambiguous": False}

    def detail(self, entry: dict[str, Any]) -> dict[str, Any]:
        volumes = [
            self._volume(entry, n) for n in range(1, entry.get("volumes", 0) + 1)
        ]
        return {
            "series": self._summary(entry),
            "inventory": {
                "filenames": [volume["filename"] for volume in volumes],
                "latest": volumes[-1] if volumes else None,
                "publicationCount": len(volumes),
                "publications": volumes,
                "totalSize": sum(volume["size"] for volume in volumes),
            },
            "providerTotals": entry.get("providerTotals", {}),
        }

    def _library_name(self, library_id: str) -> str:
        return next(
            (item["name"] for item in self.libraries if item["id"] == library_id),
            library_id,
        )

    def _in_library(self, entry: dict[str, Any], params: dict[str, str]) -> bool:
        wanted = params.get("library", "").casefold()
        names = {
            entry["libraryId"].casefold(),
            self._library_name(entry["libraryId"]).casefold(),
        }
        return not wanted or wanted in names

    def _summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "seriesId": entry["seriesId"],
            "localName": entry["localName"],
            "title": entry.get("title", entry["localName"]),
            "library": self._library_name(entry["libraryId"]),
            "libraryId": entry["libraryId"],
            "category": entry.get("category", "manga"),
            "publicationCount": entry.get("volumes", 0),
        }

    def _title_match(self, entry: dict[str, Any], query: str) -> dict[str, Any] | None:
        summary = self._summary(entry)
        for field in ("localName", "title"):
            value = summary[field]
            if query in value.casefold():
                del summary["title"]
                score = 1.0 if query == value.casefold() else len(query) / len(value)
                return {
                    **summary,
                    "matchedOn": field,
                    "matchedValue": value,
                    "score": round(score, 2),
                }
        return None

    @staticmethod
    def _has(entry: dict[str, Any], key: str, wanted: str) -> bool:
        if key == "title":
            values = [entry["localName"], entry.get("title", "")]
        else:
            # Scenarios write `authors: [...]` for the `author` filter, and so on.
            found = entry.get(f"{key}s", entry.get(key, []))
            values = found if isinstance(found, list) else [found]
        return any(wanted in str(value).casefold() for value in values)

    @staticmethod
    def _volume(entry: dict[str, Any], number: int) -> dict[str, Any]:
        name = f"{entry['localName']} {number:03}"
        return {
            "id": f"{entry['seriesId']}-v{number}",
            "number": str(number),
            "title": name,
            "filename": f"{name}.cbz",
            "pageCount": VOLUME_PAGES,
            "size": VOLUME_BYTES,
        }


class FakeBackends(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        scenario: Scenario,
        port: int = DEFAULT_PORT,
        delay: float = DEFAULT_DELAY,
        log: Callable[[str], None] = lambda line: None,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.scenario = scenario
        self.delay = delay
        self.log = log

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The default prints to stderr, which --launch shares with Cleo's screen.
        self.log(f"error    {traceback.format_exc()}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FakeBackends

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/chat":
            self._json(404, {"error": f"fake Ollama has no {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            self._chat(body)
        except (BrokenPipeError, ConnectionResetError):
            # Cleo hung up mid-stream: the user cancelled the reply.
            self.server.log("chat     client went away mid-stream")
            self.close_connection = True

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/api/tags":
            self._json(200, {"models": [{"name": "fake"}, {"name": "fake:demo"}]})
            return
        if not url.path.startswith(f"{LIBRARIAN}/"):
            self._json(404, {"detail": f"fake Nineveh has no {url.path}"})
            return
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self._json(401, {"detail": "Missing bearer token."})
            return
        route = url.path.removeprefix(LIBRARIAN)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        scenario = self.server.scenario
        if route == "/libraries":
            self._json(200, {"libraries": scenario.libraries})
        elif route == "/series":
            self._json(200, scenario.search(params))
        elif route.startswith("/series/"):
            entry = scenario.series.get(unquote(route.removeprefix("/series/")))
            if entry is None:
                self._json(404, {"detail": "No series has that ID."})
            elif "error" in entry:
                self._json(
                    entry["error"]["status"], {"detail": entry["error"].get("detail")}
                )
            else:
                self._json(200, scenario.detail(entry))
        else:
            self._json(404, {"detail": f"fake Nineveh has no {url.path}"})

    def _chat(self, body: dict[str, Any]) -> None:
        name, round_, step = self.server.scenario.next_step(body.get("messages", []))
        self.server.log(
            f"chat     rule {name!r} step {round_ + 1}: {json.dumps(step)[:100]}"
        )
        model = body.get("model", "fake")
        if "status" in step:
            self._json(step["status"], {"error": step.get("error", "scripted failure")})
            return
        self._begin_stream()
        if "raw" in step:
            self._chunk(step["raw"].encode() + b"\n")
        elif "error" in step:
            self._line({"error": step["error"]})
        elif "tool" in step:
            call = {
                "id": f"call_{round_ + 1}",
                "function": {
                    "name": step["tool"],
                    "arguments": step.get("arguments", {}),
                },
            }
            self._line(self._message(model, "", tool_calls=[call]))
        else:
            delay = step.get("delay", self.server.delay)
            for word in WORDS.findall(step.get("reply", "")):
                self._line(self._message(model, word))
                time.sleep(delay)
        if "raw" not in step and "error" not in step:
            self._line(
                {**self._message(model, ""), "done": True, "done_reason": "stop"}
            )
        self._chunk(b"")

    @staticmethod
    def _message(model: str, content: str, **extra: Any) -> dict[str, Any]:
        return {
            "model": model,
            "created_at": datetime.now(UTC).isoformat(),
            "message": {"role": "assistant", "content": content, **extra},
            "done": False,
        }

    def _begin_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _line(self, value: dict[str, Any]) -> None:
        self._chunk(json.dumps(value).encode() + b"\n")

    def _chunk(self, data: bytes) -> None:
        """One HTTP chunk. Empty data is the terminator."""
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _json(self, status: int, value: dict[str, Any]) -> None:
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        self.server.log(f"http     {format % args}")


def launch(server: FakeBackends, scenario_path: Path, state_dir: Path | None) -> int:
    """Serve in the background and run Olympus in the foreground against it."""
    if importlib.util.find_spec("olympus") is None:
        sys.exit(
            "fake_backends: olympus is not importable. Run this with the project venv."
        )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="olympus-fake-") as scratch:
        env = {
            **os.environ,
            "OLLAMA_URL": server.url,
            "NINEVEH_URL": server.url,
            FAKE_TOKEN_VARIABLE: "nvh_fake",
            # Agents added during a fake session keep their tokens in the
            # throwaway state directory, never in the real Keychain.
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            # Shown in Olympus's identity bar, so a fake session is never mistaken
            # for a real one.
            "OLYMPUS_MODEL": f"fake:{scenario_path.stem}",
            "OLYMPUS_STATE_DIR": str(state_dir or Path(scratch) / "state"),
            "OLYMPUS_BOOTSTRAP_CLEO": "1",
        }
        try:
            return subprocess.call([sys.executable, "-m", "olympus"], env=env)
        finally:
            server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario", nargs="?", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="seconds between streamed words",
    )
    parser.add_argument(
        "--launch", action="store_true", help="also run Olympus against the fakes"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="with --launch, keep Olympus history here instead of a throwaway directory",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path(tempfile.gettempdir()) / "cleo-fake-backends.log",
        help="with --launch, where requests are logged (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    scenario = Scenario.load(args.scenario)
    keywords = ", ".join(
        rule["match"] if isinstance(rule["match"], str) else "/".join(rule["match"])
        for rule in scenario.rules
    )

    if args.launch:
        with args.log.open("w", encoding="utf-8") as log_file:

            def log(line: str) -> None:
                log_file.write(f"{line}\n")
                log_file.flush()

            server = FakeBackends(scenario, args.port, args.delay, log)
            code = launch(server, args.scenario, args.state_dir)
        print(f"Fake backends logged to {args.log}")
        return code

    server = FakeBackends(
        scenario, args.port, args.delay, lambda line: print(line, flush=True)
    )
    print(
        f"Fake Ollama and Nineveh on {server.url} ({args.scenario.name})\n"
        f"Scripted keywords: {keywords}\n\n"
        "In another terminal:\n"
        f"  OLLAMA_URL={server.url} NINEVEH_URL={server.url} {FAKE_TOKEN_VARIABLE}=nvh_fake \\\n"
        f"  OLYMPUS_MODEL=fake:{args.scenario.stem} OLYMPUS_STATE_DIR=$(mktemp -d)/state \\\n"
        f"  OLYMPUS_BOOTSTRAP_CLEO=1 .venv/bin/olympus\n",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
