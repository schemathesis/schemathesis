import pytest
from hypothesis.reporting import report

from schemathesis import runner, utils
from schemathesis.cli import output


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------\ndata in section\n--------------------\n"),
        ("TEST", "*", "data in section", "******* TEST *******\ndata in section\n********************\n"),
    ],
)
def test_print_in_section(capsys, title, separator, printed, expected):
    with output.print_in_section(title, separator=separator, line_length=20):
        print(printed)

    assert capsys.readouterr().out == expected


def test_pretty_print_stats(capsys):
    output.pretty_print_stats(
        runner.StatsCollector(
            {
                "not_a_server_error": {"total": 5, "ok": 3, "error": 2},
                "different_check": {"total": 1, "ok": 1, "error": 0},
            }
        )
    )

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    assert lines[1:3] == [
        "not_a_server_error            3 / 5 passed          FAILED ",
        "different_check               1 / 1 passed          PASSED ",
    ]


def test_pretty_print_stats_empty(capsys):
    output.pretty_print_stats(runner.StatsCollector({}))
    assert capsys.readouterr().out == "No checks were performed.\n"


def test_capture_hypothesis_output():
    with utils.capture_hypothesis_output() as hypothesis_output:
        value = "Some text"
        report(value)
        report(value)
    assert hypothesis_output == [value, value]
