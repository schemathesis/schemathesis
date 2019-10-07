import pytest

from schemathesis import runner, utils
from schemathesis.cli import output


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------\ndata in section\n--------------------\n"),
        ("TEST", "*", "data in section", "******* TEST *******\ndata in section\n********************\n"),
    ],
)
def test_print_in_section(title, separator, printed, expected):
    with utils.stdout_listener() as getvalue:
        with output.print_in_section(title, separator=separator, line_length=20):
            print(printed)
        printed = getvalue()

    assert printed == expected


def test_pretty_print_stats(mocker):
    mocker.patch("schemathesis.cli.output.print_in_section")

    with utils.stdout_listener() as getvalue:
        output.pretty_print_stats(
            runner.StatsCollector(
                {
                    "not_a_server_error": {"total": 5, "ok": 3, "error": 2},
                    "different_check": {"total": 1, "ok": 1, "error": 0},
                }
            )
        )
        result = getvalue()

    assert result == (
        "not_a_server_error            3 / 5 passed          FAILED \n"
        "different_check               1 / 1 passed          PASSED \n"
    )


def test_pretty_print_stats_empty(mocker):
    mocker.patch("schemathesis.cli.output.print_in_section")

    with utils.stdout_listener() as getvalue:
        output.pretty_print_stats(runner.StatsCollector({}))
        result = getvalue()

    assert result == "No checks were performed.\n"
