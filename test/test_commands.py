from functools import partial

import pytest
from click.testing import CliRunner

from schemathesis import commands, runner


@pytest.fixture()
def schemathesis_cmd(testcmd):
    return partial(testcmd, "schemathesis")


def test_commands_help(schemathesis_cmd):
    result = schemathesis_cmd()

    assert result.ret == 0
    assert result.stdout.get_lines_after("Commands:") == ["  run  Perform schemathesis test."]

    result_help = schemathesis_cmd("--help")
    result_h = schemathesis_cmd("-h")

    assert result.stdout.lines == result_h.stdout.lines == result_help.stdout.lines


def test_commands_version(schemathesis_cmd):
    result = schemathesis_cmd("--version")

    assert result.ret == 0
    assert "version" in result.stdout.lines[0]


@pytest.mark.parametrize(
    "args, error",
    (
        (("run",), 'Error: Missing argument "SCHEMA".'),
        (("run", "not-url"), "Error: Invalid SCHEMA, must be a valid URL."),
    ),
)
def test_commands_run_errors(schemathesis_cmd, args, error):
    result = schemathesis_cmd(*args)

    assert result.ret == 2
    assert result.stderr.lines[-1] == error


def test_commands_run_help(schemathesis_cmd):
    result_help = schemathesis_cmd("run", "--help")

    assert result_help.ret == 0
    assert result_help.stdout.lines == [
        "Usage: schemathesis run [OPTIONS] SCHEMA",
        "",
        "  Perform schemathesis test against an API specified by SCHEMA.",
        "",
        "  SCHEMA must be a valid URL pointing to an Open API / Swagger",
        "  specification.",
        "",
        "Options:",
        "  -c, --checks [not_a_server_error]",
        "                                  List of checks to run.",
        "  -h, --help                      Show this message and exit.",
    ]


def test_commands_run_schema_uri(mocker):
    m_execute = mocker.patch("schemathesis.runner.execute")
    cli = CliRunner()

    schema_uri = "https://example.com/swagger.json"
    result = cli.invoke(commands.run, [schema_uri])

    assert result.exit_code == 0
    m_execute.assert_called_once_with(schema_uri, checks=runner.DEFAULT_CHECKS)
    assert result.stdout.split("\n")[:-1] == ["Running schemathesis test cases ...", "Done."]
