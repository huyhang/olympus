from cleo.commands import Command, parse_command


def test_plain_chat_is_not_a_command():
    assert parse_command("What do I have?") is None


def test_commands_are_normalized_without_changing_the_argument():
    assert parse_command("  /NaMe  Hypatia  ") == Command("name", "Hypatia")


def test_empty_command_is_still_local():
    assert parse_command("/") == Command("")
