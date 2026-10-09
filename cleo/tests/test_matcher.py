from __future__ import annotations

import asyncio

import pytest
from cleo.domain import LocalVolume, ModelChunk, SeriesOption, ToolCall, VolumeHint
from cleo.matcher import (
    ModelTitleGuesser,
    SeriesMatcher,
    clean_guess,
    confident,
    describe_match,
    options_of,
)
from cleo.ports import CatalogError, ModelError
from fakes import MemoryInbox, ScriptedModel, TitleCatalog, title_match

PLUTO = title_match("pluto", "Pluto")
SAGA = title_match("saga", "Saga", library="Comics", score=1.0, count=10)
VINLAND = title_match("vinland-saga", "Vinland Saga", score=0.33, count=12)
CONFIDENT_PLUTO = {"candidates": [PLUTO], "confidentMatch": "pluto"}
AMBIGUOUS_SAGA = {"candidates": [SAGA, VINLAND], "confidentMatch": None}


class FixedGuesser:
    def __init__(self, title):
        self.title = title
        self.asked = []

    async def guess(self, filename):
        self.asked.append(filename)
        return self.title


def run(awaitable):
    return asyncio.run(awaitable)


def matcher(files, answers, guesser=None):
    inbox = MemoryInbox(files)
    catalog = TitleCatalog(answers)
    return SeriesMatcher(catalog, inbox, guesser), catalog


def local(name):
    return LocalVolume(
        path=__import__("pathlib").Path("/in") / name, relative=name, size=1
    )


def test_comicinfo_is_tried_before_the_filename():
    found, catalog = matcher(
        {"scan_0042.cbz": (b"PK", "<ComicInfo><Series>Pluto</Series></ComicInfo>")},
        {"pluto": CONFIDENT_PLUTO},
    )
    result = run(found.match(local("scan_0042.cbz")))
    assert result.series.series_id == "pluto"
    assert (
        result.reason == "Manga · folder name match 1.00 · named in its ComicInfo.xml"
    )
    assert catalog.queries == ["Pluto"]


def test_the_filename_is_used_when_comicinfo_does_not_resolve():
    found, catalog = matcher(
        {"pluto_v3.cbz": (b"PK", "<ComicInfo><Series>Unknown</Series></ComicInfo>")},
        {"pluto": CONFIDENT_PLUTO},
    )
    result = run(found.match(local("pluto_v3.cbz")))
    assert result.series.series_id == "pluto"
    assert result.reason.endswith("read from the filename")
    assert catalog.queries == ["Unknown", "pluto"]


def test_an_ambiguous_title_offers_every_series_seen_and_asks_the_model():
    guesser = FixedGuesser(None)
    found, _ = matcher(
        {"saga_v11.cbz": (b"PK", None)}, {"saga": AMBIGUOUS_SAGA}, guesser
    )
    result = run(found.match(local("saga_v11.cbz")))
    assert result.series is None
    assert [item.series_id for item in result.alternatives] == ["saga", "vinland-saga"]
    assert (
        result.reason == "Several series could match; the catalog would not pick one."
    )
    assert guesser.asked == ["saga_v11.cbz"]


def test_a_lone_candidate_nineveh_will_not_confirm_is_offered_not_taken():
    weak = {"candidates": [title_match("pluto", "Pluto", score=0.75)]}
    found, _ = matcher({"pluto_ish.cbz": (b"PK", None)}, {"pluto ish": weak})
    result = run(found.match(local("pluto_ish.cbz")))
    assert result.series is None
    assert [item.series_id for item in result.alternatives] == ["pluto"]
    assert result.reason == "The catalog found “Pluto” but would not confirm it."


def test_the_model_supplies_a_search_term_but_nineveh_still_decides():
    guesser = FixedGuesser("Pluto")
    found, catalog = matcher(
        {"PLT_TZK_09.cbz": (b"PK", None)}, {"pluto": CONFIDENT_PLUTO}, guesser
    )
    result = run(found.match(local("PLT_TZK_09.cbz")))
    assert result.series.series_id == "pluto"
    assert result.reason.endswith("suggested by the model")
    assert catalog.queries == ["PLT TZK", "Pluto"]


@pytest.mark.parametrize(
    ("guess", "expected_queries"),
    [(None, ["mystery scan"]), ("mystery scan", ["mystery scan"])],
)
def test_a_useless_guess_is_not_searched(guess, expected_queries):
    found, catalog = matcher(
        {"mystery_scan.cbz": (b"PK", None)}, {}, FixedGuesser(guess)
    )
    result = run(found.match(local("mystery_scan.cbz")))
    assert result.series is None
    assert result.reason == "Nothing in the catalog matches “mystery scan”."
    assert catalog.queries == expected_queries


def test_a_model_guess_that_also_fails_leaves_the_choice_to_the_user():
    found, _ = matcher({"odd.cbz": (b"PK", None)}, {}, FixedGuesser("Nope"))
    assert run(found.match(local("odd.cbz"))).series is None


def test_a_name_with_no_title_cannot_be_matched():
    found, catalog = matcher({"v01.cbz": (b"PK", None)}, {})
    result = run(found.match(local("v01.cbz")))
    assert result.reason == "The filename does not name a series."
    assert catalog.queries == []


def test_catalog_failures_propagate_to_the_session():
    found, _ = matcher(
        {"pluto_v1.cbz": (b"PK", None)}, {"pluto": CatalogError("Nineveh is down.")}
    )
    with pytest.raises(CatalogError):
        run(found.match(local("pluto_v1.cbz")))


def test_search_returns_options_bounded_in_length():
    found, catalog = matcher({}, {"p" * 200: CONFIDENT_PLUTO})
    assert run(found.search("p" * 300))[0].series_id == "pluto"
    assert catalog.queries == ["p" * 200]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            CONFIDENT_PLUTO,
            [SeriesOption("pluto", "Pluto", "Manga", 1.0, "localName", 8)],
        ),
        (
            {"candidates": [{"seriesId": "x", "title": "\x1b[2JX", "score": True}]},
            [SeriesOption("x", "X")],
        ),
        ({"candidates": [{"seriesId": "y"}]}, [SeriesOption("y", "y")]),
        ({"candidates": [{"localName": "no id"}, "junk"]}, []),
        ({"candidates": "junk"}, []),
        (["not", "a", "dict"], []),
    ],
)
def test_options_are_read_defensively(payload, expected):
    assert list(options_of(payload)) == expected


def test_only_nineveh_names_a_confident_match():
    options = options_of(AMBIGUOUS_SAGA)
    assert confident(AMBIGUOUS_SAGA, options) is None
    assert confident({"confidentMatch": "saga"}, options).series_id == "saga"
    assert confident([], options) is None


@pytest.mark.parametrize(
    ("option", "hint", "expected"),
    [
        (
            SeriesOption("p", "Pluto", "Manga", 0.9, "title"),
            VolumeHint("Pluto", None, "filename"),
            "Manga · title match 0.90 · read from the filename",
        ),
        (
            SeriesOption("p", "Pluto", "", 0.5, "alternativeTitle"),
            VolumeHint("Pluto", None, "model"),
            "alternative title match 0.50 · suggested by the model",
        ),
        (
            SeriesOption("p", "Pluto"),
            VolumeHint("Pluto", None, "comicinfo"),
            "named in its ComicInfo.xml",
        ),
    ],
)
def test_a_match_explains_itself(option, hint, expected):
    assert describe_match(option, hint) == expected


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Pluto", "Pluto"),
        ('  "Vinland Saga".\nBecause the filename says so.', "Vinland Saga"),
        ("**Monster**", "Monster"),
        ("UNKNOWN", None),
        ("unknown", None),
        ("", None),
        ("x" * 121, None),
    ],
)
def test_a_model_reply_counts_only_when_it_is_plainly_a_title(reply, expected):
    assert clean_guess(reply) == expected


def test_the_guesser_asks_without_tools_and_ignores_tool_calls():
    model = ScriptedModel(
        [
            [ModelChunk("Pl"), ModelChunk("uto")],
            [ModelChunk(tool_calls=(ToolCall("search_series", {}),)), ModelChunk("x")],
        ]
    )
    guesser = ModelTitleGuesser(model)
    assert run(guesser.guess("PLT_09.cbz")) == "Pluto"
    assert run(guesser.guess("saga_v11.cbz")) is None
    messages, tools = model.requests[0]
    assert tools == [] and messages[-1] == {"role": "user", "content": "PLT_09.cbz"}


def test_a_model_failure_is_no_guess():
    class BrokenModel(ScriptedModel):
        async def stream_chat(self, messages, tools):
            raise ModelError("Ollama is unavailable.")
            yield  # pragma: no cover

    assert run(ModelTitleGuesser(BrokenModel([])).guess("x.cbz")) is None
