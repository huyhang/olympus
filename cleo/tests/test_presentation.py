from datetime import UTC

from cleo.presentation import format_timestamp, plain_text


def test_terminal_escapes_are_stripped_but_the_words_survive():
    assert plain_text("\x1b[2J\x1b[31mVinland Saga\x07") == "[2J[31mVinland Saga"
    assert plain_text("a\x00b\x7fc\x9fd") == "abcd"


def test_tabs_and_newlines_are_left_alone():
    assert plain_text("one\ttwo\nthree") == "one\ttwo\nthree"
    assert plain_text("carriage\rreturn") == "carriagereturn"


def test_timestamp_is_human_friendly():
    assert format_timestamp("2026-09-22T18:05:00+00:00", UTC) == (
        "Sep 22, 2026 at 6:05 PM UTC"
    )


def test_invalid_timestamp_is_left_visible_instead_of_crashing_the_tui():
    assert format_timestamp("unknown") == "unknown"
