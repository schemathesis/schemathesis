import pytest
from _pytest.main import ExitCode
from hypothesis import HealthCheck, Phase, Verbosity
from requests import Request, Response
from requests.auth import HTTPDigestAuth
from requests.exceptions import HTTPError

from schemathesis.runner import DEFAULT_CHECKS


def test_commands_help(cli):
    result = cli.run_subprocess()

    assert result.ret == ExitCode.OK
    assert result.stdout.get_lines_after("Commands:") == ["  run  Perform schemathesis test."]

    result_help = cli.run_subprocess("--help")
    result_h = cli.run_subprocess("-h")

    assert result.stdout.lines == result_h.stdout.lines == result_help.stdout.lines


def test_commands_version(cli):
    result = cli.run_subprocess("--version")

    assert result.ret == ExitCode.OK
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
            ("run", "http://127.0.0.1", "--auth=:pass"),
            'Error: Invalid value for "--auth" / "-a": Username should not be empty',
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
        (
            ("run", "http://127.0.0.1", "--hypothesis-phases=explicit,first,second"),
            'Error: Invalid value for "--hypothesis-phases": invalid choice(s): first, second. '
            "Choose from explicit, reuse, generate, shrink",
        ),
    ),
)
def test_commands_run_errors(cli, args, error):
    # When invalid arguments are passed to CLI
    result = cli.run_subprocess(*args)

    # Then an appropriate error should be displayed
    assert result.ret == ExitCode.INTERRUPTED
    assert result.stderr.lines[-1] == error


def test_commands_run_help(cli):
    result_help = cli.run_subprocess("run", "--help")

    assert result_help.ret == ExitCode.OK
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
        "  -T, --tag TEXT                  Filter schemathesis test by schema tag",
        "                                  pattern.",
        "  -b, --base-url TEXT             Base URL address of the API.",
        "  --hypothesis-deadline INTEGER   Duration in milliseconds that each",
        "                                  individual example with a test is not",
        "                                  allowed to exceed.",
        "  --hypothesis-derandomize        Use Hypothesis's deterministic mode.",
        "  --hypothesis-max-examples INTEGER",
        "                                  Maximum number of generated examples per",
        "                                  each method/endpoint combination.",
        "  --hypothesis-phases [explicit|reuse|generate|shrink]",
        "                                  Control which phases should be run.",
        "  --hypothesis-report-multiple-bugs BOOLEAN",
        "                                  Raise only the exception with the smallest",
        "                                  minimal example.",
        "  --hypothesis-suppress-health-check [data_too_large|filter_too_much|too_slow|return_value|"
        "hung_test|large_base_example|not_a_test_method]",
        "                                  Comma-separated list of health checks to",
        "                                  disable.",
        "  --hypothesis-verbosity [quiet|normal|verbose|debug]",
        "                                  Verbosity level of Hypothesis messages",
        "  -h, --help                      Show this message and exit.",
    ]


SCHEMA_URI = "https://example.com/swagger.json"


@pytest.mark.parametrize(
    "args, expected",
    (
        ([SCHEMA_URI], {"checks": DEFAULT_CHECKS}),
        ([SCHEMA_URI, "--auth=test:test"], {"checks": DEFAULT_CHECKS, "api_options": {"auth": ("test", "test")}}),
        (
            [SCHEMA_URI, "--auth=test:test", "--auth-type=digest"],
            {"checks": DEFAULT_CHECKS, "api_options": {"auth": HTTPDigestAuth("test", "test")}},
        ),
        (
            [SCHEMA_URI, "--auth=test:test", "--auth-type=DIGEST"],
            {"checks": DEFAULT_CHECKS, "api_options": {"auth": HTTPDigestAuth("test", "test")}},
        ),
        (
            [SCHEMA_URI, "--header=Authorization:Bearer 123"],
            {"checks": DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123"}}},
        ),
        (
            [SCHEMA_URI, "--header=Authorization:  Bearer 123 "],
            {"checks": DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123 "}}},
        ),
        (
            [SCHEMA_URI, "--method=POST", "--method", "GET"],
            {"checks": DEFAULT_CHECKS, "loader_options": {"method": ("POST", "GET")}},
        ),
        ([SCHEMA_URI, "--endpoint=users"], {"checks": DEFAULT_CHECKS, "loader_options": {"endpoint": ("users",)}}),
        ([SCHEMA_URI, "--tag=foo"], {"checks": DEFAULT_CHECKS, "loader_options": {"tag": ("foo",)}}),
        (
            [SCHEMA_URI, "--base-url=https://example.com/api/v1test"],
            {"checks": DEFAULT_CHECKS, "api_options": {"base_url": "https://example.com/api/v1test"}},
        ),
        (
            [
                SCHEMA_URI,
                "--hypothesis-deadline=1000",
                "--hypothesis-derandomize",
                "--hypothesis-max-examples=1000",
                "--hypothesis-phases=explicit,generate",
                "--hypothesis-report-multiple-bugs=0",
                "--hypothesis-suppress-health-check=too_slow,filter_too_much",
                "--hypothesis-verbosity=normal",
            ],
            {
                "checks": DEFAULT_CHECKS,
                "hypothesis_options": {
                    "deadline": 1000,
                    "derandomize": True,
                    "max_examples": 1000,
                    "phases": [Phase.explicit, Phase.generate],
                    "report_multiple_bugs": False,
                    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.filter_too_much],
                    "verbosity": Verbosity.normal,
                },
            },
        ),
    ),
)
def test_execute_arguments(cli, mocker, args, expected):
    m_execute = mocker.patch("schemathesis.runner.prepare", autospec=True)

    result = cli.run_inprocess(*args)

    assert result.exit_code == 0
    m_execute.assert_called_once_with(SCHEMA_URI, **expected)


@pytest.mark.endpoints()
def test_hypothesis_parameters(cli, schema_url):
    # When Hypothesis options are passed via command line
    result = cli.run_inprocess(
        schema_url,
        "--hypothesis-deadline=1000",
        "--hypothesis-derandomize",
        "--hypothesis-max-examples=1000",
        "--hypothesis-phases=explicit,generate",
        "--hypothesis-report-multiple-bugs=0",
        "--hypothesis-suppress-health-check=too_slow,filter_too_much",
        "--hypothesis-verbosity=normal",
    )
    # Then they should be correctly converted into arguments accepted by `hypothesis.settings`
    # Parameters are validated in `hypothesis.settings`
    assert result.exit_code == 0


@pytest.mark.endpoints("success")
def test_cli_run_output_success(cli, schema_url):
    result = cli.run_inprocess(schema_url)
    assert result.exit_code == 0
    assert "GET /api/success ." in result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.split("\n")
    assert "Tests succeeded." in lines


def test_cli_run_output_with_errors(cli, schema_url):
    result = cli.run_inprocess(schema_url)
    assert result.exit_code == 1
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.split("\n")
    assert "not_a_server_error            1 / 3 passed          FAILED " in lines
    assert "Tests failed." in lines


@pytest.mark.endpoints()
def test_cli_run_output_empty(cli, schema_url):
    result = cli.run_inprocess(schema_url)
    assert result.exit_code == 0
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.split("\n")
    assert "No checks were performed." in lines
    assert "Tests succeeded." in lines


@pytest.mark.parametrize(
    "status_code, message",
    (
        (404, f"Schema was not found at {SCHEMA_URI}"),
        (500, f"Failed to load schema, code 500 was returned via {SCHEMA_URI}"),
    ),
)
def test_execute_missing_schema(cli, mocker, status_code, message):
    response = Response()
    response.status_code = status_code
    request = Request(url=SCHEMA_URI)
    mocker.patch("schemathesis.runner.prepare", side_effect=(HTTPError(response=response, request=request)))
    result = cli.run_inprocess(SCHEMA_URI)
    assert result.exit_code == 1
    assert message in result.stdout


@pytest.mark.endpoints("success", "slow")
def test_hypothesis_failed_event(cli, schema_url):
    # When the Hypothesis deadline option is set manually and it is smaller than the response time
    result = cli.run_inprocess(schema_url, "--hypothesis-deadline=20")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == 1
    # And the given endpoint should be displayed as an error
    assert "GET /api/slow E" in result.stdout
    # And the proper error message from Hypothesis should be displayed
    assert "hypothesis.errors.DeadlineExceeded: Test took " in result.stdout
    assert "which exceeds the deadline of 20.00ms" in result.stdout


@pytest.mark.endpoints("success", "slow")
def test_default_hypothesis_settings(cli, schema_url):
    # When there is a slow endpoint and if it is faster than 500ms
    result = cli.run_inprocess(schema_url)
    # Then the tests should pass, because of default 500ms deadline
    assert result.exit_code == 0
    assert "GET /api/success ." in result.stdout
    assert "GET /api/slow ." in result.stdout


@pytest.mark.endpoints("unsatisfiable")
def test_unsatisfiable(cli, schema_url):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters definition - requires to be integer AND string at the
    # same time
    result = cli.run_inprocess(schema_url)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == 1
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    assert "POST /api/unsatisfiable E" in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Unsatisfiable: Unable to satisfy schema parameters for this endpoint" in lines


@pytest.mark.endpoints("flaky")
def test_flaky(cli, schema_url):
    # When the endpoint fails / succeeds randomly
    result = cli.run_inprocess(schema_url)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == 1
    # And standard Hypothesis error should not appear in the output
    assert "Failed to reproduce exception. Expected:" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    assert "GET /api/flaky E" in result.stdout
    # And it should be displayed only once in "ERRORS" section
    assert "= ERRORS =" in result.stdout
    assert "_ GET: /api/flaky _" in result.stdout
    # And it should not go into "FAILURES" section
    assert "= FAILURES =" not in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Flaky: Tests on this endpoint produce unreliable results: " in lines
    assert "Falsified on the first call but did not on a subsequent one" in lines


@pytest.mark.endpoints("invalid")
def test_invalid_endpoint(cli, schema_url):
    # When the app's schema contains errors
    # For example if it type is "int" but should be "integer"
    result = cli.run_inprocess(schema_url)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == 1
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    assert "POST /api/invalid E" in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "schemathesis.exceptions.InvalidSchema: Invalid schema for this endpoint" in lines


def test_connection_error(cli, schema_url):
    # When the given base_url is unreachable
    result = cli.run_inprocess(schema_url, "--base-url=http://127.0.0.1:1/")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == 1
    # And all collected endpoints should be marked as errored
    assert "GET /api/failure E" in result.stdout
    assert "GET /api/success E" in result.stdout
    # And errors section title should be displayed
    assert "= ERRORS =" in result.stdout
    # And all endpoints should be mentioned in this section as subsections
    assert "_ GET: /api/success _" in result.stdout
    assert "_ GET: /api/failure _" in result.stdout
    # And the proper error messages should be displayed for each endpoint
    assert "Max retries exceeded with url: /api/success" in result.stdout
    assert "Max retries exceeded with url: /api/failure" in result.stdout
