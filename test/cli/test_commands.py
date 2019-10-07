from functools import partial

import pytest
from click.testing import CliRunner

from schemathesis import cli, runner


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
        (
            ("run", "http://127.0.0.1", "--auth=123"),
            'Error: Invalid value for "--auth" / "-a": Should be in KEY:VALUE format. Got: 123',
        ),
        (
            ("run", "http://127.0.0.1", "--header=123"),
            'Error: Invalid value for "--header" / "-H": Should be in KEY:VALUE format. Got: 123',
        ),
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
        "  -a, --auth TEXT                 Server user and password. Example:",
        "                                  USER:PASSWORD",
        "  -H, --header TEXT               Custom header in a that will be used in all",
        r"                                  requests to the server. Example:",
        r"                                  Authorization: Bearer\ 123",
        r"  -E, --endpoint TEXT             Filter schemathesis test by endpoint",
        r"                                  pattern. Example: users/\d+",
        "  -M, --method TEXT               Filter schemathesis test by HTTP method.",
        "  -h, --help                      Show this message and exit.",
    ]


SCHEMA_URI = "https://example.com/swagger.json"


@pytest.mark.parametrize(
    "args, expected",
    (
        ([SCHEMA_URI], {"checks": runner.DEFAULT_CHECKS}),
        (
            [SCHEMA_URI, "--auth=test:test"],
            {"checks": runner.DEFAULT_CHECKS, "api_options": {"auth": ("test", "test")}},
        ),
        (
            [SCHEMA_URI, "--header=Authorization:Bearer 123"],
            {"checks": runner.DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123"}}},
        ),
        (
            [SCHEMA_URI, "--header=Authorization:  Bearer 123 "],
            {"checks": runner.DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123 "}}},
        ),
        (
            [SCHEMA_URI, "--method=POST", "--method", "GET"],
            {"checks": runner.DEFAULT_CHECKS, "loader_options": {"method": ("POST", "GET")}},
        ),
        (
            [SCHEMA_URI, "--endpoint=users"],
            {"checks": runner.DEFAULT_CHECKS, "loader_options": {"endpoint": ("users",)}},
        ),
    ),
)
def test_commands_run(mocker, args, expected):
    m_execute = mocker.patch("schemathesis.runner.execute")
    cli_runner = CliRunner()

    schema_uri = "https://example.com/swagger.json"
    result = cli_runner.invoke(cli.run, args)

    assert result.exit_code == 0
    m_execute.assert_called_once_with(schema_uri, **expected)


@pytest.mark.parametrize(
    "data,line_in_output",
    (
        ({"not_a_server_error": {"total": 1, "ok": 1, "error": 0}}, "Tests succeeded."),
        ({}, "No checks were performed."),
        ({"not_a_server_error": {"total": 3, "ok": 1, "error": 2}}, "Tests failed."),
    ),
)
def test_commands_run_output(mocker, data, line_in_output):
    m_execute = mocker.patch("schemathesis.runner.execute", return_value=mocker.Mock(data=data, is_empty=not data))

    cli_runner = CliRunner()
    result = cli_runner.invoke(cli.run, [SCHEMA_URI])

    stdout_lines = result.stdout.split("\n")
    assert "Running schemathesis test cases ..." in stdout_lines
    assert line_in_output in stdout_lines
