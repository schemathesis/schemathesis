import os
from test.utils import HERE, SIMPLE_PATH

import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import HealthCheck, Phase, Verbosity
from requests import Request, Response
from requests.auth import HTTPDigestAuth
from requests.exceptions import HTTPError

from schemathesis import Case
from schemathesis.loaders import from_path
from schemathesis.runner import DEFAULT_CHECKS


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
            "Choose from explicit, reuse, generate, shrink",
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
        "content_type_conformance|response_schema_conformance]",
        "                                  List of checks to run.",
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
        "  -b, --base-url TEXT             Base URL address of the API, required for",
        "                                  SCHEMA if specified by file.",
        "  --request-timeout INTEGER       Timeout in milliseconds for network requests",
        "                                  during the test run.",
        "  --hypothesis-deadline INTEGER   Duration in milliseconds that each individual",
        "                                  example with a test is not allowed to exceed.",
        "  --hypothesis-derandomize        Use Hypothesis's deterministic mode.",
        "  --hypothesis-max-examples INTEGER",
        "                                  Maximum number of generated examples per each",
        "                                  method/endpoint combination.",
        "  --hypothesis-phases [explicit|reuse|generate|shrink]",
        "                                  Control which phases should be run.",
        "  --hypothesis-report-multiple-bugs BOOLEAN",
        "                                  Raise only the exception with the smallest",
        "                                  minimal example.",
        "  --hypothesis-seed INTEGER       Set a seed to use for all Hypothesis tests.",
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
        (
            [SIMPLE_PATH, "--base-url=http://127.0.0.1"],
            {"checks": DEFAULT_CHECKS, "loader_options": {"base_url": "http://127.0.0.1"}, "loader": from_path},
        ),
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
            {"checks": DEFAULT_CHECKS, "loader_options": {"base_url": "https://example.com/api/v1test"}},
        ),
        ([SCHEMA_URI, "--hypothesis-seed=123"], {"checks": DEFAULT_CHECKS, "seed": 123}),
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


@pytest.mark.endpoints("success")
def test_cli_run_output_success(cli, schema_url):
    result = cli.run(schema_url)
    assert result.exit_code == ExitCode.OK
    assert "GET /api/success ." in result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    last_line = lines[-1]
    assert "== 1 passed in " in last_line
    # And the running time is a small positive number
    time = float(last_line.split(" ")[-2].replace("s", ""))
    assert 0 < time < 5


def test_cli_run_output_with_errors(cli, schema_url):
    result = cli.run(schema_url)
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "Received a response with 5xx status code: 500" in lines
    assert "not_a_server_error            1 / 3 passed          FAILED " in lines
    assert f"== 1 passed, 1 failed in " in lines[-1]


@pytest.mark.endpoints("failure")
def test_cli_run_only_failure(cli, schema_url):
    result = cli.run(schema_url)
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "not_a_server_error            0 / 2 passed          FAILED " in lines
    assert "== 1 failed in " in lines[-1]


@pytest.mark.endpoints()
def test_cli_run_output_empty(cli, schema_url):
    result = cli.run(schema_url)
    assert result.exit_code == ExitCode.OK
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "No checks were performed." in lines
    assert "= Empty test suite =" in lines[-1]


@pytest.mark.endpoints()
def test_cli_run_changed_base_url(cli, schema_url, server):
    # When the CLI receives custom base URL
    base_url = f"http://127.0.0.1:{server['port']}/api/"
    result = cli.run(schema_url, "--base-url", base_url)
    # Then the base URL should be correctly displayed in the CLI output
    lines = result.stdout.strip().split("\n")
    assert lines[-9] == f"Base URL: {base_url}"


@pytest.mark.parametrize(
    "status_code, message",
    (
        (404, f"Schema was not found at {SCHEMA_URI}"),
        (500, f"Failed to load schema, code 500 was returned from {SCHEMA_URI}"),
    ),
)
def test_execute_missing_schema(cli, mocker, status_code, message):
    response = Response()
    response.status_code = status_code
    request = Request(url=SCHEMA_URI)
    mocker.patch("schemathesis.runner.prepare", side_effect=(HTTPError(response=response, request=request)))
    result = cli.run(SCHEMA_URI)
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert message in result.stdout


@pytest.mark.endpoints("success", "slow")
def test_hypothesis_failed_event(cli, schema_url):
    # When the Hypothesis deadline option is set manually and it is smaller than the response time
    result = cli.run(schema_url, "--hypothesis-deadline=20")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And the given endpoint should be displayed as an error
    assert "GET /api/slow E" in result.stdout
    # And the proper error message from Hypothesis should be displayed
    assert "hypothesis.errors.DeadlineExceeded: Test took " in result.stdout
    assert "which exceeds the deadline of 20.00ms" in result.stdout


@pytest.mark.endpoints("success", "slow")
def test_connection_timeout(cli, server, schema_url):
    # When connection timeout is specified in the CLI and the request fails because of it
    result = cli.run(schema_url, "--request-timeout=1")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And the given endpoint should be displayed as an error
    assert "GET /api/slow E" in result.stdout
    # And the proper error message should be displayed
    assert (
        f"requests.exceptions.ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port={server['port']}): "
        "Read timed out. (read timeout=0.001)" in result.stdout
    )


@pytest.mark.endpoints("success", "slow")
def test_default_hypothesis_settings(cli, schema_url):
    # When there is a slow endpoint and if it is faster than 500ms
    result = cli.run(schema_url)
    # Then the tests should pass, because of default 500ms deadline
    assert result.exit_code == ExitCode.OK
    assert "GET /api/success ." in result.stdout
    assert "GET /api/slow ." in result.stdout


@pytest.mark.endpoints("failure")
def test_seed(cli, schema_url):
    # When there is a failure
    result = cli.run(schema_url, "--hypothesis-seed=456")
    # Then the tests should fail and RNG seed should be displayed
    assert result.exit_code == 1
    assert "Or add this option to your command line parameters: --hypothesis-seed=456" in result.stdout.split("\n")


@pytest.mark.endpoints("unsatisfiable")
def test_unsatisfiable(cli, schema_url):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters definition - requires to be integer AND string at the
    # same time
    result = cli.run(schema_url)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
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
    # Derandomize is needed for reproducible test results
    result = cli.run(schema_url, "--hypothesis-derandomize")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
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
    # And example is displayed
    assert "Query           : {'id': 0}" in lines


@pytest.mark.endpoints("invalid")
def test_invalid_endpoint(cli, schema_url):
    # When the app's schema contains errors
    # For example if its type is "int" but should be "integer"
    result = cli.run(schema_url)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this endpoint should be marked as errored in the progress line
    assert "POST /api/invalid E" in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "schemathesis.exceptions.InvalidSchema: Invalid schema for this endpoint" in lines


@pytest.mark.endpoints("teapot")
def test_status_code_conformance(cli, schema_url):
    # When endpoint returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    result = cli.run(schema_url, "-c", "status_code_conformance")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And this endpoint should be marked as failed in the progress line
    assert "POST /api/teapot F" in result.stdout
    assert "status_code_conformance            0 / 2 passed          FAILED" in result.stdout
    lines = result.stdout.split("\n")
    assert "Received a response with a status code, which is not defined in the schema: 418" in lines
    assert lines[15].strip() == "Declared status codes: 200"


def test_connection_error(cli, schema_url):
    # When the given base_url is unreachable
    result = cli.run(schema_url, "--base-url=http://127.0.0.1:1/")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
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


def test_schema_not_available(cli):
    # When the given schema is unreachable
    result = cli.run("http://127.0.0.1:1/swagger.yaml")
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


def make_importable(module):
    """Make the package importable by the inline CLI execution."""
    pkgroot = module.dirpath()
    module._ensuresyspath(True, pkgroot)


@pytest.mark.endpoints("custom_format")
def test_pre_run_hook_valid(testdir, cli, schema_url, app):
    # When `--pre-run` hook is passed to the CLI call
    module = testdir.makepyfile(
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
    make_importable(module)

    result = cli.main("--pre-run", module.purebasename, "run", schema_url)

    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK
    # And all registered new string format should produce digits as expected
    assert all(request.query["id"].isdigit() for request in app["incoming_requests"])


def test_pre_run_hook_invalid(testdir, cli):
    # When `--pre-run` hook is passed to the CLI call
    # And its importing causes an exception
    module = testdir.makepyfile(hook="1 / 0")
    make_importable(module)

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
    module = testdir.makepyfile(
        hook="""
        import schemathesis

        @schemathesis.register_check
        def new_check(response, result):
            raise AssertionError("Custom check failed!")
        """
    )
    make_importable(module)

    result = cli.main("--pre-run", module.purebasename, "run", "-c", "new_check", schema_url)

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED
    # And a message from the new check should be displayed
    lines = result.stdout.strip().split("\n")
    assert lines[13] == "Custom check failed!"


def test_keyboard_interrupt(testdir, cli, schema_url, base_url, mocker):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    original = Case("/success", "GET", base_url=base_url).call
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    result = cli.run(schema_url)
    assert result.exit_code == ExitCode.OK
    # Then execution stops and a message about interruption is displayed
    lines = result.stdout.strip().split("\n")
    assert lines[9].startswith("GET /api/failure .")
    assert lines[9].endswith("[ 50%]")
    assert lines[10] == "GET /api/success "
    assert "!! KeyboardInterrupt !!" in lines[11]
    # And summary is still displayed in the end of the output
    assert "== SUMMARY ==" in lines[13]


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
            "teapot": {
                "post": {
                    "parameters": [
                        {
                            # during the CLI run we have a different working directory, so specifying an absolute file path
                            "schema": {"$ref": os.path.join(HERE, "data/petstore_v2.yaml#/definitions/Pet")},
                            "in": "body",
                            "name": "user",
                            "required": True,
                        }
                    ]
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
