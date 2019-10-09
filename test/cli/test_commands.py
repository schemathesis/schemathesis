from contextlib import contextmanager
from functools import partial

import pytest
from click.testing import CliRunner
from requests.auth import HTTPDigestAuth

from schemathesis import cli, runner


@pytest.fixture()
def cli_runner():
    return CliRunner()


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
            ("run", "http://127.0.0.1", "--auth-type=random"),
            'Error: Invalid value for "--auth-type" / "-A": invalid choice: random. (choose from basic, digest)',
        ),
        (
            ("run", "http://127.0.0.1", "--header=123"),
            'Error: Invalid value for "--header" / "-H": Should be in KEY:VALUE format. Got: 123',
        ),
        (
            ("run", "http://127.0.0.1", "--header=:"),
            'Error: Invalid value for "--header" / "-H": Header name should not be empty',
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
        "  -A, --auth-type [basic|digest]  The authentication mechanism to be used.",
        "                                  Defaults to 'basic'.",
        "  -H, --header TEXT               Custom header in a that will be used in all",
        r"                                  requests to the server. Example:",
        r"                                  Authorization: Bearer\ 123",
        r"  -E, --endpoint TEXT             Filter schemathesis test by endpoint",
        r"                                  pattern. Example: users/\d+",
        "  -M, --method TEXT               Filter schemathesis test by HTTP method.",
        "  -b, --base-url TEXT             Base URL address of the API.",
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
            [SCHEMA_URI, "--auth=test:test", "--auth-type=digest"],
            {"checks": runner.DEFAULT_CHECKS, "api_options": {"auth": HTTPDigestAuth("test", "test")}},
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
        (
            [SCHEMA_URI, "--base-url=https://example.com/api/v1test"],
            {"checks": runner.DEFAULT_CHECKS, "api_options": {"base_url": "https://example.com/api/v1test"}},
        ),
    ),
)
def test_commands_run(cli_runner, mocker, args, expected):
    m_execute = mocker.patch("schemathesis.runner.execute")

    result = cli_runner.invoke(cli.run, args)

    assert result.exit_code == 0
    m_execute.assert_called_once_with(SCHEMA_URI, **expected)


@pytest.mark.parametrize(
    "data,line_in_output,exit_code",
    (
        ({"not_a_server_error": {"total": 1, "ok": 1, "error": 0}}, "Tests succeeded.", 0),
        ({}, "No checks were performed.", 0),
        ({"not_a_server_error": {"total": 3, "ok": 1, "error": 2}}, "Tests failed.", 1),
    ),
)
def test_commands_run_output(cli_runner, mocker, data, line_in_output, exit_code):
    mocker.patch("schemathesis.runner.execute", return_value=mocker.Mock(data=data, is_empty=not data))

    result = cli_runner.invoke(cli.run, [SCHEMA_URI])
    assert result.exit_code == exit_code

    stdout_lines = result.stdout.split("\n")
    assert "Running schemathesis test cases ..." in stdout_lines
    assert line_in_output in stdout_lines


def test_commands_with_hypothesis_statistic(cli_runner, mocker):
    mocker.patch(
        "schemathesis.runner.execute",
        return_value=mocker.Mock(data={"not_a_server_error": {"total": 3, "ok": 1, "error": 2}}, is_empty=False),
    )

    @contextmanager
    def mocked_listener():
        yield lambda: "Mock error"

    mocker.patch("schemathesis.utils.stdout_listener", mocked_listener)

    result = cli_runner.invoke(cli.run, [SCHEMA_URI])
    assert result.exit_code == 1
    assert " FALSIFYING EXAMPLES " in result.stdout
    assert "Mock error" in result.stdout
    assert " SUMMARY " in result.stdout
