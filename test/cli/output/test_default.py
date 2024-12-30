import click
import pytest

from schemathesis.cli.output import default


@pytest.mark.parametrize(
    ("title", "separator", "expected"),
    [
        ("TEST", "-", "------- TEST -------"),
        ("TEST", "*", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, expected):
    # When section name is displayed
    default.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = default.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert expected in out


@pytest.mark.parametrize(
    ("position", "length", "expected"), [(1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")]
)
def test_get_percentage(position, length, expected):
    assert default.get_percentage(position, length) == expected
