import os
import time
from test.utils import HERE, SIMPLE_PATH

import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import HealthCheck, Phase, Verbosity
from requests import Response

from schemathesis import Case
from schemathesis._compat import metadata
from schemathesis.checks import ALL_CHECKS
from schemathesis.loaders import from_path
from schemathesis.models import Endpoint
from schemathesis.runner import DEFAULT_CHECKS

PHASES = "explicit, reuse, generate, target, shrink"
if metadata.version("hypothesis") < "4.5":
    PHASES = "explicit, reuse, generate, shrink"
HEALTH_CHECKS = "data_too_large|filter_too_much|too_slow|return_value|large_base_example|not_a_test_method"
if metadata.version("hypothesis") < "5.0":
    HEALTH_CHECKS = (
        "data_too_large|filter_too_much|too_slow|return_value|hung_test|large_base_example|not_a_test_method"
    )


def test_commands_help(cli):
    result = cli.main()

    assert result.exit_code == ExitCode.OK
    lines = result.stdout.split("\n")
    assert lines[11] == "  run  Perform schemathesis test."

    result_help = cli.main("--help")
    result_h = cli.main("-h")

    assert result.stdout == result_h.stdout == result_help.stdout


def test_run_subprocess(testdir):
    # To verify that CLI entry point is installed properly
    result = testdir.run("schemathesis")
    assert result.ret == ExitCode.OK


def test_commands_version(cli):
    result = cli.main("--version")

    assert result.exit_code == ExitCode.OK
    assert "version" in result.stdout.split("\n")[0]


@pytest.mark.parametrize(
    "args, error",
    (
        (("run",), 'Error: Missing argument "SCHEMA".'),
        (("run", "not-url"), "Error: Invalid SCHEMA, must be a valid URL or file path."),
        (("run", SIMPLE_PATH), 'Error: Missing argument, "--base-url" is required for SCHEMA specified by file.'),
        (("run", SIMPLE_PATH, "--base-url=test"), "Error: Invalid base URL"),
        (("run", SIMPLE_PATH, "--base-url=127.0.0.1:8080"), "Error: Invalid base URL"),
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
            f"Choose from {PHASES}",
        ),
        (
            ("run", "http://127.0.0.1", "--hypothesis-deadline=wrong"),
            'Error: Invalid value for "--hypothesis-deadline": wrong is not a valid integer or None',
        ),
    ),
)
def test_commands_run_errors(cli, args, error):
    # When invalid arguments are passed to CLI
    result = cli.main(*args)

    # Then an appropriate error should be displayed
    assert result.exit_code == ExitCode.INTERRUPTED
    assert result.stdout.strip().split("\n")[-1] == error


def test_commands_run_help(cli):
    result_help = cli.main("run", "--help")

    assert result_help.exit_code == ExitCode.OK
    assert result_help.stdout.strip().split("\n") == [
        "Usage: schemathesis run [OPTIONS] SCHEMA",
        "",
        "  Perform schemathesis test against an API specified by SCHEMA.",
        "",
        "  SCHEMA must be a valid URL or file path pointing to an Open API / Swagger",
        "  specification.",
        "",
        "Options:",
        "  -c, --checks [not_a_server_error|status_code_conformance|"
        "content_type_conformance|response_schema_conformance|all]",
        "                                  List of checks to run.",
        "  -x, --exitfirst                 Exit instantly on first error or failed test.",
        "  -a, --auth TEXT                 Server user and password. Example:",
        "                                  USER:PASSWORD",
        "  -A, --auth-type [basic|digest]  The authentication mechanism to be used.",
        "                                  Defaults to 'basic'.",
        "  -H, --header TEXT               Custom header in a that will be used in all",
        r"                                  requests to the server. Example:",
        r"                                  Authorization: Bearer\ 123",
        r"  -E, --endpoint TEXT             Filter schemathesis test by endpoint pattern.",
        r"                                  Example: users/\d+",
        "  -M, --method TEXT               Filter schemathesis test by HTTP method.",
        "  -T, --tag TEXT                  Filter schemathesis test by schema tag",
        "                                  pattern.",
        "  -w, --workers INTEGER RANGE     Number of workers to run tests",
        "  -b, --base-url TEXT             Base URL address of the API, required for",
        "                                  SCHEMA if specified by file.",
        "  --app TEXT                      WSGI application to test",
        "  --request-timeout INTEGER       Timeout in milliseconds for network requests",
        "                                  during the test run.",
        "  --validate-schema BOOLEAN       Enable or disable validation of input schema.",
        "  --show-errors-tracebacks        Show full tracebacks for internal errors.",
        "  --hypothesis-deadline INTEGER   Duration in milliseconds that each individual",
        "                                  example with a test is not allowed to exceed.",
        "  --hypothesis-derandomize        Use Hypothesis's deterministic mode.",
        "  --hypothesis-max-examples INTEGER",
        "                                  Maximum number of generated examples per each",
        "                                  method/endpoint combination.",
        f"  --hypothesis-phases [{PHASES.replace(', ', '|')}]",
        "                                  Control which phases should be run.",
        "  --hypothesis-report-multiple-bugs BOOLEAN",
        "                                  Raise only the exception with the smallest",
        "                                  minimal example.",
        "  --hypothesis-seed INTEGER       Set a seed to use for all Hypothesis tests.",
        f"  --hypothesis-suppress-health-check [{HEALTH_CHECKS}]",
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
        ([SCHEMA_URI], {"checks": DEFAULT_CHECKS, "workers_num": 1}),
        ([SCHEMA_URI, "--checks=all"], {"checks": ALL_CHECKS, "workers_num": 1}),
        ([SCHEMA_URI, "--exitfirst"], {"checks": DEFAULT_CHECKS, "exit_first": True, "workers_num": 1}),
        (
            [SIMPLE_PATH, "--base-url=http://127.0.0.1"],
            {
                "checks": DEFAULT_CHECKS,
                "loader_options": {"base_url": "http://127.0.0.1"},
                "loader": from_path,
                "workers_num": 1,
            },
        ),
        (
            [SCHEMA_URI, "--auth=test:test"],
            {
                "checks": DEFAULT_CHECKS,
                "api_options": {"auth": ("test", "test"), "auth_type": "basic"},
                "workers_num": 1,
            },
        ),
        (
            [SCHEMA_URI, "--auth=test:test", "--auth-type=digest"],
            {
                "checks": DEFAULT_CHECKS,
                "api_options": {"auth": ("test", "test"), "auth_type": "digest"},
                "workers_num": 1,
            },
        ),
        (
            [SCHEMA_URI, "--auth=test:test", "--auth-type=DIGEST"],
            {
                "checks": DEFAULT_CHECKS,
                "api_options": {"auth": ("test", "test"), "auth_type": "digest"},
                "workers_num": 1,
            },
        ),
        (
            [SCHEMA_URI, "--header=Authorization:Bearer 123"],
            {"checks": DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123"}}, "workers_num": 1},
        ),
        (
            [SCHEMA_URI, "--header=Authorization:  Bearer 123 "],
            {"checks": DEFAULT_CHECKS, "api_options": {"headers": {"Authorization": "Bearer 123 "}}, "workers_num": 1},
        ),
        (
            [SCHEMA_URI, "--method=POST", "--method", "GET"],
            {"checks": DEFAULT_CHECKS, "loader_options": {"method": ("POST", "GET")}, "workers_num": 1},
        ),
        (
            [SCHEMA_URI, "--endpoint=users"],
            {"checks": DEFAULT_CHECKS, "loader_options": {"endpoint": ("users",)}, "workers_num": 1},
        ),
        ([SCHEMA_URI, "--tag=foo"], {"checks": DEFAULT_CHECKS, "loader_options": {"tag": ("foo",)}, "workers_num": 1}),
        (
            [SCHEMA_URI, "--base-url=https://example.com/api/v1test"],
            {
                "checks": DEFAULT_CHECKS,
                "loader_options": {"base_url": "https://example.com/api/v1test"},
                "workers_num": 1,
            },
        ),
        ([SCHEMA_URI, "--hypothesis-seed=123"], {"checks": DEFAULT_CHECKS, "seed": 123, "workers_num": 1}),
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
                "workers_num": 1,
            },
        ),
        (
            [SCHEMA_URI, "--hypothesis-deadline=None"],
            {"checks": DEFAULT_CHECKS, "hypothesis_options": {"deadline": None}, "workers_num": 1},
        ),
    ),
)
def test_execute_arguments(cli, mocker, args, expected):
    m_execute = mocker.patch("schemathesis.runner.prepare", autospec=True)

    result = cli.run(*args)

    assert result.exit_code == ExitCode.OK
    m_execute.assert_called_once_with(args[0], **expected)


@pytest.mark.endpoints()
def test_hypothesis_parameters(cli, schema_url):
    # When Hypothesis options are passed via command line
    result = cli.run(
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
    assert result.exit_code == ExitCode.OK


def pytest_generate_tests(metafunc):
    """Generate all proper combinations for running CLI.

    It should be runnable by single/multiple workers and running instance/WSGI app.
    """
    if "cli_args" in metafunc.fixturenames:
        metafunc.parametrize("cli_args", ["wsgi", "real"], indirect=True)


@pytest.fixture
def cli_args(request):
    if request.param == "real":
        schema_url = request.getfixturevalue("schema_url")
        args = (schema_url,)
    else:
        app_path = request.getfixturevalue("loadable_flask_app")
        args = (f"--app={app_path}", "/swagger.yaml")
    return args


@pytest.mark.endpoints("success")
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_success(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK
    lines = result.stdout.split("\n")
    assert lines[7] == f"Workers: {workers}"
    if workers == 1:
        assert lines[10].startswith("GET /api/success .")
    else:
        assert lines[10] == "."
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    last_line = lines[-1]
    assert "== 1 passed in " in last_line
    # And the running time is a small positive number
    time = float(last_line.split(" ")[-2].replace("s", ""))
    assert 0 < time < 5


@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_with_errors(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "1. Received a response with 5xx status code: 500" in lines
    assert "not_a_server_error            1 / 3 passed          FAILED " in lines
    assert f"== 1 passed, 1 failed in " in lines[-1]


@pytest.mark.endpoints("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_only_failure(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "not_a_server_error            0 / 2 passed          FAILED " in lines
    assert "== 1 failed in " in lines[-1]


@pytest.mark.endpoints("upload_file")
def test_cli_binary_body(cli, schema_url):
    result = cli.run(schema_url, "--hypothesis-suppress-health-check=filter_too_much")
    assert result.exit_code == ExitCode.OK
    assert " HYPOTHESIS OUTPUT " not in result.stdout


@pytest.mark.endpoints()
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_empty(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "No checks were performed." in lines
    assert "= Empty test suite =" in lines[-1]


@pytest.mark.endpoints()
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_changed_base_url(cli, server, cli_args, workers):
    # When the CLI receives custom base URL
    base_url = f"http://127.0.0.1:{server['port']}/api/"
    result = cli.run(*cli_args, "--base-url", base_url, f"--workers={workers}")
    # Then the base URL should be correctly displayed in the CLI output
    lines = result.stdout.strip().split("\n")
    assert lines[-10] == f"Base URL: {base_url}"


@pytest.mark.parametrize(
    "status_code, message",
    (
        (404, f"Schema was not found at {SCHEMA_URI}"),
        (500, f"Failed to load schema, code 500 was returned from {SCHEMA_URI}"),
    ),
)
@pytest.mark.parametrize("workers", (1, 2))
def test_execute_missing_schema(cli, mocker, status_code, message, workers):
    response = Response()
    response.status_code = status_code
    mocker.patch("schemathesis.loaders.requests.get", autospec=True, return_value=response)
    result = cli.run(SCHEMA_URI, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert message in result.stdout


@pytest.mark.endpoints("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_hypothesis_failed_event(cli, cli_args, workers):
    # When the Hypothesis deadline option is set manually and it is smaller than the response time
    result = cli.run(*cli_args, "--hypothesis-deadline=20", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And the given endpoint should be displayed as an error
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow E")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10].split("\n")[0] in ("E.", ".E")
        # empty line after all tests progress output
        assert lines[11] == ""
    # And the proper error message from Hypothesis should be displayed
    assert "hypothesis.errors.DeadlineExceeded: Test took " in result.stdout
    assert "which exceeds the deadline of 20.00ms" in result.stdout


@pytest.mark.endpoints("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_connection_timeout(cli, server, schema_url, workers):
    # When connection timeout is specified in the CLI and the request fails because of it
    result = cli.run(schema_url, "--request-timeout=10", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And the given endpoint should be displayed as an error
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow E")
        assert lines[11].startswith("GET /api/success .")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10].split("\n")[0] in ("E.", ".E")
    # And the proper error message should be displayed
    assert (
        f"requests.exceptions.ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port={server['port']}): "
        "Read timed out. (read timeout=0.01)" in result.stdout
    )


@pytest.mark.endpoints("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_default_hypothesis_settings(cli, cli_args, workers):
    # When there is a slow endpoint and if it is faster than 500ms
    result = cli.run(*cli_args, f"--workers={workers}")
    # Then the tests should pass, because of default 500ms deadline
    assert result.exit_code == ExitCode.OK
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow .")
        assert lines[11].startswith("GET /api/success .")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10] == ".."


@pytest.mark.endpoints("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_seed(cli, cli_args, workers):
    # When there is a failure
    result = cli.run(*cli_args, "--hypothesis-seed=456", f"--workers={workers}")
    # Then the tests should fail and RNG seed should be displayed
    assert result.exit_code == 1
    assert "Or add this option to your command line parameters: --hypothesis-seed=456" in result.stdout.split("\n")


@pytest.mark.endpoints("unsatisfiable")
@pytest.mark.parametrize("workers", (1, 2))
def test_unsatisfiable(cli, cli_args, workers):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters definition - requires to be integer AND string at the
    # same time
    result = cli.run(*cli_args, f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/unsatisfiable E")
    else:
        assert lines[10] == "E"
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Unsatisfiable: Unable to satisfy schema parameters for this endpoint" in lines


@pytest.mark.endpoints("flaky")
@pytest.mark.parametrize("workers", (1, 2))
def test_flaky(cli, cli_args, workers):
    # When the endpoint fails / succeeds randomly
    # Derandomize is needed for reproducible test results
    result = cli.run(*cli_args, "--hypothesis-derandomize", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And standard Hypothesis error should not appear in the output
    assert "Failed to reproduce exception. Expected:" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/flaky E")
    else:
        assert lines[10] == "E"
    # And it should be displayed only once in "ERRORS" section
    assert "= ERRORS =" in result.stdout
    assert "_ GET: /api/flaky _" in result.stdout
    # And it should not go into "FAILURES" section
    assert "= FAILURES =" not in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Flaky: Tests on this endpoint produce unreliable results: " in lines
    assert "Falsified on the first call but did not on a subsequent one" in lines
    # And example is displayed
    assert "Query           : {'id': 0}" in lines


@pytest.mark.endpoints("invalid")
@pytest.mark.parametrize("workers", (1, 2))
def test_invalid_endpoint(cli, cli_args, workers):
    # When the app's schema contains errors
    # For example if its type is "int" but should be "integer"
    # And schema validation is disabled
    result = cli.run(*cli_args, f"--workers={workers}", "--validate-schema=false")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/invalid E")
    else:
        assert lines[10] == "E"
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "schemathesis.exceptions.InvalidSchema: Invalid schema for this endpoint" in lines


@pytest.mark.endpoints("teapot")
@pytest.mark.parametrize("workers", (1, 2))
def test_status_code_conformance(cli, cli_args, workers):
    # When endpoint returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    result = cli.run(*cli_args, "-c", "status_code_conformance", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And this endpoint should be marked as failed in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/teapot F")
    else:
        assert lines[10] == "F"
    assert "status_code_conformance            0 / 2 passed          FAILED" in result.stdout
    lines = result.stdout.split("\n")
    assert "1. Received a response with a status code, which is not defined in the schema: 418" in lines
    assert lines[16].strip() == "Declared status codes: 200"


@pytest.mark.endpoints("multiple_failures")
def test_multiple_failures_single_check(cli, schema_url):
    result = cli.run(schema_url, "--hypothesis-derandomize")

    assert "= HYPOTHESIS OUTPUT =" not in result.stdout
    assert "Hypothesis found 2 distinct failures" not in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "1. Received a response with 5xx status code: 500" in lines
    assert "2. Received a response with 5xx status code: 504" in lines
    assert "1 failed in " in lines[-1]


@pytest.mark.endpoints("multiple_failures")
def test_multiple_failures_different_check(cli, schema_url):
    result = cli.run(
        schema_url, "-c", "status_code_conformance", "-c", "not_a_server_error", "--hypothesis-derandomize"
    )

    assert "= HYPOTHESIS OUTPUT =" not in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "1. Received a response with a status code, which is not defined in the schema: 500" in lines
    assert "2. Received a response with 5xx status code: 500" in lines
    assert "3. Received a response with a status code, which is not defined in the schema: 504" in lines
    assert "4. Received a response with 5xx status code: 504" in lines
    assert "1 failed in " in lines[-1]


@pytest.mark.parametrize("workers", (1, 2))
def test_connection_error(cli, schema_url, workers):
    # When the given base_url is unreachable
    result = cli.run(schema_url, "--base-url=http://127.0.0.1:1/", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And all collected endpoints should be marked as errored
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/failure E")
        assert lines[11].startswith("GET /api/success E")
    else:
        assert lines[10] == "EE"
    # And errors section title should be displayed
    assert "= ERRORS =" in result.stdout
    # And all endpoints should be mentioned in this section as subsections
    assert "_ GET: /api/success _" in result.stdout
    assert "_ GET: /api/failure _" in result.stdout
    # And the proper error messages should be displayed for each endpoint
    assert "Max retries exceeded with url: /api/success" in result.stdout
    assert "Max retries exceeded with url: /api/failure" in result.stdout


@pytest.mark.parametrize("workers", (1, 2))
def test_schema_not_available(cli, workers):
    # When the given schema is unreachable
    result = cli.run("http://127.0.0.1:1/swagger.yaml", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And error message is displayed
    lines = result.stdout.split("\n")
    assert lines[0] == "Failed to load schema from http://127.0.0.1:1/swagger.yaml"
    assert lines[1].startswith(
        "Error: requests.exceptions.ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=1): "
        "Max retries exceeded with url: /swagger.yaml"
    )
    assert lines[-2] == "Aborted!"


def test_schema_not_available_wsgi(cli, loadable_flask_app):
    # When the given schema is unreachable
    result = cli.run("unknown.yaml", f"--app={loadable_flask_app}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And error message is displayed
    lines = result.stdout.split("\n")
    assert lines[0] == "Schema was not found at unknown.yaml"
    assert lines[1] == "Aborted!"


@pytest.mark.endpoints("custom_format")
def test_pre_run_hook_valid(testdir, cli, schema_url, app):
    # When `--pre-run` hook is passed to the CLI call
    module = testdir.make_importable_pyfile(
        hook="""
    import string
    import schemathesis
    from hypothesis import strategies as st

    schemathesis.register_string_format(
        "digits",
        st.text(
            min_size=1,
            alphabet=st.characters(
                whitelist_characters=string.digits,
                whitelist_categories=()
            )
        )
    )
    """
    )

    result = cli.main(
        "--pre-run", module.purebasename, "run", "--hypothesis-suppress-health-check=filter_too_much", schema_url
    )

    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK
    # And all registered new string format should produce digits as expected
    assert all(request.query["id"].isdigit() for request in app["incoming_requests"])


def test_pre_run_hook_invalid(testdir, cli):
    # When `--pre-run` hook is passed to the CLI call
    # And its importing causes an exception
    module = testdir.make_importable_pyfile(hook="1 / 0")

    result = cli.main("--pre-run", module.purebasename, "run", "http://127.0.0.1:1")

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And a helpful message should be displayed in the output
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "An exception happened during the hook loading:"
    assert lines[7] == "ZeroDivisionError: division by zero"
    assert lines[9] == "Aborted!"


@pytest.mark.endpoints("success")
def test_register_check(testdir, cli, schema_url):
    # When `--pre-run` hook is passed to the CLI call
    # And it contains registering a new check, which always fails for the testing purposes
    module = testdir.make_importable_pyfile(
        hook="""
        import schemathesis

        @schemathesis.register_check
        def new_check(response, result):
            raise AssertionError("Custom check failed!")
        """
    )

    result = cli.main("--pre-run", module.purebasename, "run", "-c", "new_check", schema_url)

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And a message from the new check should be displayed
    lines = result.stdout.strip().split("\n")
    assert lines[14] == "1. Custom check failed!"


def assert_threaded_executor_interruption(lines, expected, optional_interrupt=False):
    # It is possible to have a case when first call without an error will start processing
    # But after, another thread will have interruption and will push this event before the
    # first thread will finish. Race condition: "" is for this case and "." for the other
    # way around
    assert lines[10] in expected
    if not optional_interrupt:
        assert "!! KeyboardInterrupt !!" in lines[11]
    if "F" in lines[10]:
        # assert "=== FAILURES ===" in lines[12]
        if "!! KeyboardInterrupt !!" not in lines[11]:
            assert "=== FAILURES ===" in lines[12]
            position = 23
        else:
            assert "=== FAILURES ===" in lines[13]
            position = 24
    else:
        position = 13
    assert "== SUMMARY ==" in lines[position]


@pytest.mark.parametrize("workers", (1, 2))
def test_keyboard_interrupt(cli, cli_args, base_url, mocker, flask_app, swagger_20, workers):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    endpoint = Endpoint("/success", "GET", {}, swagger_20, base_url=base_url)
    if len(cli_args) == 2:
        endpoint.app = flask_app
        original = Case(endpoint).call_wsgi
    else:
        original = Case(endpoint).call
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            # For threaded case it emulates SIGINT for the worker thread
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    if len(cli_args) == 2:
        mocker.patch("schemathesis.Case.call_wsgi", wraps=mocked)
    else:
        mocker.patch("schemathesis.Case.call", wraps=mocked)
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK
    # Then execution stops and a message about interruption is displayed
    lines = result.stdout.strip().split("\n")
    # And summary is still displayed in the end of the output
    if workers == 1:
        assert lines[10].startswith("GET /api/failure .")
        assert lines[10].endswith("[ 50%]")
        assert lines[11] == "GET /api/success "
        assert "!! KeyboardInterrupt !!" in lines[12]
        assert "== SUMMARY ==" in lines[14]
    else:
        assert_threaded_executor_interruption(lines, ("", "."))


def test_keyboard_interrupt_threaded(cli, cli_args, mocker):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    original = time.sleep
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.runner.time.sleep", autospec=True, wraps=mocked)
    result = cli.run(*cli_args, "--workers=2")
    # the exit status depends on what thread finished first
    assert result.exit_code in (ExitCode.OK, ExitCode.TESTS_FAILED)
    # Then execution stops and a message about interruption is displayed
    lines = result.stdout.strip().split("\n")
    # There are many scenarios possible, depends how many tests will be executed before interruption
    # and in what order. it could be no tests at all, some of them or all of them.
    assert_threaded_executor_interruption(lines, ("F", ".", "F.", ".F", ""), True)


@pytest.mark.endpoints("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_hypothesis_output_capture(mocker, cli, cli_args, workers):
    mocker.patch("schemathesis.utils.IGNORED_PATTERNS", ())

    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert "= HYPOTHESIS OUTPUT =" in result.stdout
    assert "Falsifying example" in result.stdout


async def test_multiple_files_schema(app, testdir, cli, base_url):
    # When the schema contains references to other files
    schema = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "produces": ["application/json"],
        "paths": {
            "/teapot": {
                "post": {
                    "parameters": [
                        {
                            # during the CLI run we have a different working directory,
                            # so specifying an absolute file path
                            "schema": {"$ref": os.path.join(HERE, "data/petstore_v2.yaml#/definitions/Pet")},
                            "in": "body",
                            "name": "user",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    app["config"].update({"should_fail": True, "schema_data": schema})
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    # And file path is given to the CLI
    result = cli.run(
        str(schema_file), f"--base-url={base_url}", "--hypothesis-max-examples=5", "--hypothesis-derandomize"
    )
    # Then Schemathesis should resolve it and run successfully
    assert result.exit_code == ExitCode.OK
    # And all relevant requests should contain proper data for resolved references
    payload = await app["incoming_requests"][0].json()
    assert isinstance(payload["name"], str)
    assert isinstance(payload["photoUrls"], list)


def test_wsgi_app(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps._flask import create_app

        app = create_app()
        """
    )
    result = cli.run("/swagger.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_exception(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps._flask import create_app

        1 / 0
        """
    )
    result = cli.run("/swagger.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert result.stdout == "Error: ZeroDivisionError: division by zero\n\nAborted!\n"


def test_wsgi_app_missing(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps._flask import create_app
        """
    )
    result = cli.run("/swagger.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.INTERRUPTED
    assert (
        result.stdout.strip().split("\n")[-1]
        == 'Error: Invalid value for "--app": Can not import application from the given module'
    )


def test_wsgi_app_internal_exception(testdir, cli, caplog):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps._flask import create_app

        app = create_app()
        app.config["internal_exception"] = True
        """
    )
    result = cli.run("/swagger.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED
    lines = result.stdout.strip().split("\n")
    assert "== APPLICATION LOGS ==" in lines[34]
    assert "ERROR in app: Exception on /api/success [GET]" in lines[36]
    assert lines[52] == "ZeroDivisionError: division by zero"


@pytest.mark.parametrize("args", ((), ("--base-url",)))
def test_aiohttp_app(request, testdir, cli, loadable_aiohttp_app, args):
    # When an URL is passed together with app
    if args:
        args += (request.getfixturevalue("base_url"),)
    result = cli.run("/swagger.yaml", "--app", loadable_aiohttp_app, *args)
    # Then the schema should be loaded from that URL
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_remote_schema(testdir, cli, schema_url, loadable_flask_app):
    # When an URL is passed together with app
    result = cli.run(schema_url, "--app", loadable_flask_app)
    # Then the schema should be loaded from that URL
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_path_schema(testdir, cli, loadable_flask_app):
    # When an existing path to schema is passed together with app
    result = cli.run(SIMPLE_PATH, "--app", loadable_flask_app)
    # Then the schema should be loaded from that path
    assert result.exit_code == ExitCode.OK
    assert "1 passed in" in result.stdout
