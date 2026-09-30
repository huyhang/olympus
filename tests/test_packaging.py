"""Guards on how the repository is packaged and installed."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text(encoding="utf-8"))


def test_one_distribution_ships_olympus_and_cleo_under_a_unique_name():
    project = load("pyproject.toml")["project"]
    # PyPI's `olympus` is an unrelated project; installing by that name, or
    # depending on it, would fetch the wrong software.
    assert project["name"] == "olympus-agents"
    assert project["scripts"] == {"olympus": "olympus.cli:main"}
    assert project["entry-points"]["olympus.agents"] == {
        "cleo": "cleo.provider:CleoProvider"
    }
    wheel = load("pyproject.toml")["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/olympus", "cleo/src/cleo"]


def test_cleo_is_not_a_second_distribution():
    cleo = load("cleo/pyproject.toml")
    assert "project" not in cleo and "build-system" not in cleo
    assert "pytest" in cleo["tool"]
