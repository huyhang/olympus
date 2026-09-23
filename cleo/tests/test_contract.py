"""The vendored contract must still describe the Nineveh that is running.

Nineveh proves its own committed document matches its live route table. This
is the other half: that the copy Cleo builds against has not fallen behind it.

Skipped rather than failed when Nineveh is unreachable, because this should
run on every local `pytest` and is useless if it blocks work on a train.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import httpx
import pytest

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
CONTRACT = CONTRACTS / "librarian-openapi.json"
SOURCE = CONTRACTS / "SOURCE"
NINEVEH = os.environ.get("NINEVEH_URL", "http://localhost:8080")
METHODS = {"get", "post", "put", "patch", "delete"}
SCHEMA_REF = re.compile(r'"\$ref":\s*"#/components/schemas/([^"]+)"')


def librarian_paths(document: dict) -> dict:
    """The same filter Nineveh's exporter applies, so the two are comparable."""
    filtered = {
        path: {
            method: operation
            for method, operation in item.items()
            if isinstance(operation, dict)
            and "librarian" in (operation.get("tags") or [])
        }
        for path, item in document["paths"].items()
    }
    return {path: item for path, item in filtered.items() if item}


@pytest.fixture(scope="module")
def vendored() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def declared(field: str) -> str:
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{field} missing from contracts/SOURCE")


def test_the_recorded_hash_matches_the_file_beside_it():
    """Catches a contract edited in place without refreshing its provenance."""
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == declared("sha256")


def test_the_recorded_operation_count_matches():
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    counted = sum(
        1 for item in document["paths"].values() for method in item if method in METHODS
    )
    assert counted == int(declared("operations"))


def test_every_reference_resolves_inside_the_vendored_copy(vendored: dict):
    """A dangling $ref fails at code generation, far later than it needs to."""
    carried = set(vendored.get("components", {}).get("schemas", {}))
    referenced = set(SCHEMA_REF.findall(json.dumps(vendored)))
    assert referenced - carried == set()


def test_the_contract_carries_no_administration_endpoints(vendored: dict):
    """Token management is the operator's surface, not Cleo's."""
    assert not [path for path in vendored["paths"] if "/admin/" in path]


def test_every_operation_asks_for_a_token_the_copy_declares(vendored: dict):
    """Cleo always sends its token; a client generated from this copy sends one
    only where an operation requires it, by a scheme defined here."""
    schemes = set(vendored.get("components", {}).get("securitySchemes", {}))
    required = [
        {name for requirement in operation.get("security", []) for name in requirement}
        for item in vendored["paths"].values()
        for method, operation in item.items()
        if method in METHODS
    ]
    assert required
    assert all(names and names <= schemes for names in required)


@pytest.fixture(scope="module")
def live() -> dict:
    try:
        return httpx.get(f"{NINEVEH}/openapi.json", timeout=3).json()
    except httpx.HTTPError as unreachable:
        pytest.skip(f"Nineveh not reachable at {NINEVEH}: {unreachable}")


STALE = "contracts/librarian-openapi.json is stale — run scripts/refresh-contract.sh"


def test_the_vendored_paths_still_match_nineveh(vendored: dict, live: dict):
    assert librarian_paths(live) == vendored["paths"], STALE


def test_the_vendored_components_still_match_nineveh(vendored: dict, live: dict):
    """A path names its bodies and its token only by reference, so a renamed
    response field, or a changed auth scheme, leaves every path as it was."""
    for section in ("schemas", "securitySchemes"):
        carried = vendored.get("components", {}).get(section, {})
        published = live.get("components", {}).get(section, {})
        assert {name: published.get(name) for name in carried} == carried, STALE
