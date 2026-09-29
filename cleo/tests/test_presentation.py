from datetime import UTC

from cleo.presentation import format_timestamp, plain_text, streamable


def test_terminal_escapes_are_stripped_but_the_words_survive():
    assert plain_text("\x1b[2J\x1b[31mVinland Saga\x07") == "Vinland Saga"
    assert plain_text("a\x00b\x7fc\x9fd") == "abcd"


def test_every_kind_of_escape_sequence_goes_whole():
    # Colour, cursor and erase controls, in both spellings.
    assert plain_text("\x1b[1;31mred\x1b[0m \x1b[?25l\x9b2Jgone") == "red gone"
    # Window titles and hyperlinks, whose payload is never shown either.
    assert plain_text("\x1b]0;evil title\x07ok") == "ok"
    assert plain_text("\x1b]8;;https://evil.test\x1b\\click\x1b]8;;\x1b\\") == "click"
    assert plain_text("\x9d0;evil title\x9cok") == "ok"
    assert plain_text("\x1bPq#0;2;0;0;0\x1b\\ok \x1b_payload\x1b\\") == "ok "
    # Resets, cursor saves and character-set switches.
    assert plain_text("\x1bc\x1b7saved\x1b8\x1b(B") == "saved"


def test_a_malformed_sequence_loses_its_introducer_and_nothing_more():
    assert plain_text("\x1b]0;never terminated") == "0;never terminated"
    assert plain_text("cut off \x1b[31") == "cut off 31"


def test_tabs_and_newlines_are_left_alone():
    assert plain_text("one\ttwo\nthree") == "one\ttwo\nthree"
    assert plain_text("carriage\rreturn") == "carriagereturn"


def test_an_unfinished_sequence_is_held_back_until_it_completes():
    assert streamable("red \x1b") == "red "
    assert streamable("red \x1b[3") == "red "
    assert streamable("red \x1b[31mblue") == "red blue"
    assert streamable("a\x1b]0;half a tit") == "a"
    assert streamable("a\x1b]0;title\x1b") == "a"  # the terminator half-arrived
    assert streamable("a\x1b]0;title\x1b\\b") == "ab"
    assert streamable("plain words") == "plain words"


def test_streamed_text_only_grows_and_never_shows_what_the_answer_will_not():
    """However a text is split into chunks, the draft stays a prefix of the answer."""
    for text in (
        "You hold \x1b[1;31mPluto\x1b[0m.",
        "a\x1b]8;;https://evil.test\x1b\\link\x1b]8;;\x1b\\ b\x9b2Jc",
        "\x1b]0;never terminated, then words",
        "cut off \x1b[31",
    ):
        shown = ""
        for end in range(len(text) + 1):
            visible = streamable(text[:end])
            assert visible.startswith(shown), (text, end)
            shown = visible
        assert plain_text(text).startswith(shown)


def test_timestamp_is_human_friendly():
    assert format_timestamp("2026-09-22T18:05:00+00:00", UTC) == (
        "Sep 22, 2026 at 6:05 PM UTC"
    )


def test_invalid_timestamp_is_left_visible_instead_of_crashing_the_tui():
    assert format_timestamp("unknown") == "unknown"
