import base64
import json
import os
import pathlib
import sys
import time
from test.apps.openapi.schema import OpenAPIVersion
from test.utils import HERE, SIMPLE_PATH
from urllib.parse import urljoin

import hypothesis
import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import HealthCheck, Phase, Verbosity

from schemathesis import Case, DataGenerationMethod, fixups, service
from schemathesis.checks import ALL_CHECKS
from schemathesis.cli import LoaderConfig, execute, get_exit_code, reset_checks
from schemathesis.constants import DEFAULT_RESPONSE_TIMEOUT, USER_AGENT, CodeSampleStyle
from schemathesis.hooks import unregister_all
from schemathesis.models import APIOperation
from schemathesis.runner import DEFAULT_CHECKS, from_schema
from schemathesis.runner.impl import threadpool
from schemathesis.targets import DEFAULT_TARGETS

PHASES = ", ".join(map(lambda x: x.name, Phase))
HEALTH_CHECKS = "|".join(map(lambda x: x.name, HealthCheck))


def test_commands_help(cli):
    result = cli.main()

    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    assert lines[11] == "  replay  Replay requests from a saved cassette."
    assert lines[12] == "  run     Perform schemathesis test."

    result_help = cli.main("--help")
    result_h = cli.main("-h")

    assert result.stdout == result_h.stdout == result_help.stdout


def test_run_subprocess(testdir):
    # To verify that CLI entry point is installed properly
    result = testdir.run("schemathesis")
    assert result.ret == ExitCode.OK


def test_commands_version(cli):
    result = cli.main("--version")

    assert result.exit_code == ExitCode.OK, result.stdout
    assert "version" in result.stdout.split("\n")[0]


@pytest.mark.parametrize(
    "args, error",
    (
        (("run",), "Error: Missing argument 'SCHEMA'."),
        (("run", "not-url"), "Error: Invalid SCHEMA, must be a valid URL or file path."),
        (("run", SIMPLE_PATH), 'Error: Missing argument, "--base-url" is required for SCHEMA specified by file.'),
        (("run", SIMPLE_PATH, "--base-url=test"), "Error: Invalid base URL"),
        (("run", SIMPLE_PATH, "--base-url=127.0.0.1:8080"), "Error: Invalid base URL"),
        (
            ("run", "http://127.0.0.1", "--request-timeout=-5"),
            "Error: Invalid value for '--request-timeout': -5 is smaller than the minimum valid value 1.",
        ),
        (
            ("run", "http://127.0.0.1", "--request-timeout=0"),
            "Error: Invalid value for '--request-timeout': 0 is smaller than the minimum valid value 1.",
        ),
        (
            ("run", "http://127.0.0.1", "--method=+"),
            "Error: Invalid value for '--method' / '-M': Invalid regex: nothing to repeat at position 0",
        ),
        (
            ("run", "http://127.0.0.1", "--auth=123"),
            "Error: Invalid value for '--auth' / '-a': Should be in KEY:VALUE format. Got: 123",
        ),
        (
            ("run", "http://127.0.0.1", "--auth=:pass"),
            "Error: Invalid value for '--auth' / '-a': Username should not be empty",
        ),
        (
            ("run", "http://127.0.0.1", "--auth=тест:pass"),
            "Error: Invalid value for '--auth' / '-a': Username should be latin-1 encodable",
        ),
        (
            ("run", "http://127.0.0.1", "--auth=user:тест"),
            "Error: Invalid value for '--auth' / '-a': Password should be latin-1 encodable",
        ),
        (
            ("run", "http://127.0.0.1", "--auth-type=random"),
            "Error: Invalid value for '--auth-type' / '-A': invalid choice: random. (choose from basic, digest)",
        ),
        (
            ("run", "http://127.0.0.1", "--header=123"),
            "Error: Invalid value for '--header' / '-H': Should be in KEY:VALUE format. Got: 123",
        ),
        (
            ("run", "http://127.0.0.1", "--header=:"),
            "Error: Invalid value for '--header' / '-H': Header name should not be empty",
        ),
        (
            ("run", "http://127.0.0.1", "--header= :"),
            "Error: Invalid value for '--header' / '-H': Header name should not be empty",
        ),
        (
            ("run", "http://127.0.0.1", "--hypothesis-phases=explicit,first,second"),
            "Error: Invalid value for '--hypothesis-phases': invalid choice(s): first, second. "
            f"Choose from {PHASES}",
        ),
        (
            ("run", "http://127.0.0.1", "--hypothesis-deadline=wrong"),
            "Error: Invalid value for '--hypothesis-deadline': wrong is not a valid integer or None",
        ),
        (
            ("run", "http://127.0.0.1", "--hypothesis-deadline=0"),
            "Error: Invalid value for '--hypothesis-deadline': 0 is not in the valid range of 1 to 86399999913600000.",
        ),
        (
            ("run", "http://127.0.0.1", "--header=тест:test"),
            "Error: Invalid value for '--header' / '-H': Header name should be latin-1 encodable",
        ),
        (
            ("run", "http://127.0.0.1", "--header=test:тест"),
            "Error: Invalid value for '--header' / '-H': Header value should be latin-1 encodable",
        ),
        (("run", "//test"), "Error: Invalid SCHEMA, must be a valid URL or file path."),
        (
            ("run", "http://127.0.0.1", "--max-response-time=0"),
            "Error: Invalid value for '--max-response-time': 0 is smaller than the minimum valid value 1.",
        ),
    ),
)
def test_commands_run_errors(cli, args, error):
    # When invalid arguments are passed to CLI
    result = cli.main(*args)

    # Then an appropriate error should be displayed
    assert result.exit_code == ExitCode.INTERRUPTED, result.stdout
    assert result.stdout.strip().split("\n")[-1] == error


def test_commands_run_help(cli):
    result_help = cli.main("run", "--help")

    assert result_help.exit_code == ExitCode.OK, result_help.stdout
    assert result_help.stdout.strip().split("\n") == [
        "Usage: schemathesis run [OPTIONS] SCHEMA",
        "",
        "  Perform schemathesis test against an API specified by SCHEMA.",
        "",
        "  SCHEMA must be a valid URL or file path pointing to an Open API / GraphQL",
        "  specification.",
        "",
        "Filtering options:",
        "",
        "  These options define what parts of the API will be tested.",
        "",
        "  -E, --endpoint TEXT           Filter schemathesis tests by API operation path",
        "                                pattern. Example: users/\\d+",
        "",
        "  -M, --method TEXT             Filter schemathesis tests by HTTP method.",
        "  -T, --tag TEXT                Filter schemathesis tests by schema tag pattern.",
        "  -O, --operation-id TEXT       Filter schemathesis tests by operationId",
        "                                pattern.",
        "",
        "  --skip-deprecated-operations  Skip testing of deprecated API operations.",
        "                                [default: False]",
        "",
        "",
        "Validation options:",
        "",
        "  Options, responsible for how responses & schemas will be checked.",
        "",
        "  -c, --checks [not_a_server_error|status_code_conformance|"
        "content_type_conformance|response_headers_conformance|response_schema_conformance|all]",
        "                                  List of checks to run.  [default:",
        "                                  not_a_server_error]",
        "",
        "  --max-response-time INTEGER RANGE",
        "                                  A custom check that will fail if the response",
        "                                  time is greater than the specified one in",
        "                                  milliseconds.",
        "",
        "  --validate-schema BOOLEAN       Enable or disable validation of input schema.",
        "                                  [default: True]",
        "",
        "",
        "Hypothesis options:",
        "",
        "  Configuration of the underlying Hypothesis engine.",
        "",
        "  --hypothesis-deadline INTEGER RANGE",
        "                                  Duration in milliseconds that each individual",
        "                                  example with a test is not allowed to exceed.",
        "",
        "  --hypothesis-derandomize        Use Hypothesis's deterministic mode.",
        "  --hypothesis-max-examples INTEGER RANGE",
        "                                  Maximum number of generated examples per each",
        "                                  method/path combination.",
        "",
        f"  --hypothesis-phases [{PHASES.replace(', ', '|')}]",
        "                                  Control which phases should be run.",
        "  --hypothesis-report-multiple-bugs BOOLEAN",
        "                                  Raise only the exception with the smallest",
        "                                  minimal example.",
        "",
        "  --hypothesis-seed INTEGER       Set a seed to use for all Hypothesis tests.",
        f"  --hypothesis-suppress-health-check [{HEALTH_CHECKS}]",
        "                                  Comma-separated list of health checks to",
        "                                  disable.",
        "",
        "  --hypothesis-verbosity [quiet|normal|verbose|debug]",
        "                                  Verbosity level of Hypothesis messages.",
        "",
        "Generic options:",
        "  -D, --data-generation-method [positive|negative]",
        "                                  Defines how Schemathesis generates data for",
        "                                  tests.  [default: positive]",
        "",
        "  -t, --target [response_time|all]",
        "                                  Targets for input generation.  [default: ]",
        "  -x, --exitfirst                 Exit instantly on first error or failed test.",
        "                                  [default: False]",
        "",
        "  --dry-run                       Disable sending data to the application and",
        "                                  checking responses. Helpful to verify whether",
        "                                  data is generated at all.",
        "",
        "  -a, --auth TEXT                 Server user and password. Example:",
        "                                  USER:PASSWORD",
        "",
        "  -A, --auth-type [basic|digest]  The authentication mechanism to be used.",
        "                                  Defaults to 'basic'.  [default: basic]",
        "",
        "  -H, --header TEXT               Custom header that will be used in all",
        "                                  requests to the server. Example:",
        r"                                  Authorization: Bearer\ 123",
        "",
        "  -w, --workers [auto|1-64]       Number of workers to run tests.  [default: 1]",
        "  -b, --base-url TEXT             Base URL address of the API, required for",
        "                                  SCHEMA if specified by file.",
        "",
        "  --app TEXT                      WSGI/ASGI application to test.",
        "  --request-timeout INTEGER RANGE",
        "                                  Timeout in milliseconds for network requests",
        "                                  during the test run.",
        "",
        "  --request-tls-verify TEXT       Controls whether Schemathesis verifies the",
        "                                  server's TLS certificate. You can also pass",
        "                                  the path to a CA_BUNDLE file for private",
        "                                  certs.  [default: true]",
        "",
        "  --junit-xml FILENAME            Create junit-xml style report file at given",
        "                                  path.",
        "",
        "  --debug-output-file FILENAME    Save debug output as JSON lines in the given",
        "                                  file.",
        "",
        "  --show-errors-tracebacks        Show full tracebacks for internal errors.",
        "                                  [default: False]",
        "",
        "  --code-sample-style [python|curl]",
        "                                  Controls the style of code samples for failure",
        "                                  reproduction.",
        "",
        "  --store-network-log FILENAME    Store requests and responses into a file.",
        "  --fixups [fast_api|all]         Install specified compatibility fixups.",
        "  --stateful [links]              Utilize stateful testing capabilities.",
        "  --stateful-recursion-limit INTEGER RANGE",
        "                                  Limit recursion depth for stateful testing.",
        "                                  [default: 5]",
        "",
        "  --force-schema-version [20|30]  Force Schemathesis to parse the input schema",
        "                                  with the specified spec version.",
        "",
        "  --no-color                      Disable ANSI color escape codes.",
        "  --schemathesis-io-token TEXT    Schemathesis.io authentication token. If",
        "                                  present, test run results will be uploaded to",
        "                                  Schemathesis.io",
        "",
        "  --schemathesis-io-url TEXT      Schemathesis.io base URL.",
        "  -v, --verbosity                 Reduce verbosity of error output.",
        "  -h, --help                      Show this message and exit.",
    ]


SCHEMA_URI = "https://example.com/swagger.json"


@pytest.mark.parametrize(
    "args, expected",
    (
        ([], {}),
        (["--exitfirst"], {"exit_first": True}),
        (["--workers=2"], {"workers_num": 2}),
        (["--hypothesis-seed=123"], {"seed": 123}),
        (
            [
                "--hypothesis-deadline=1000",
                "--hypothesis-derandomize",
                "--hypothesis-max-examples=1000",
                "--hypothesis-phases=explicit,generate",
                "--hypothesis-report-multiple-bugs=0",
                "--hypothesis-suppress-health-check=too_slow,filter_too_much",
                "--hypothesis-verbosity=normal",
            ],
            {
                "hypothesis_settings": hypothesis.settings(
                    deadline=1000,
                    derandomize=True,
                    max_examples=1000,
                    phases=[Phase.explicit, Phase.generate],
                    report_multiple_bugs=False,
                    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
                    verbosity=Verbosity.normal,
                )
            },
        ),
        (["--hypothesis-deadline=None"], {"hypothesis_settings": hypothesis.settings(deadline=None)}),
        (["--max-response-time=10"], {"max_response_time": 10}),
    ),
)
def test_from_schema_arguments(cli, mocker, swagger_20, args, expected):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)

    result = cli.run(SCHEMA_URI, *args)

    expected = {
        "checks": DEFAULT_CHECKS,
        "targets": DEFAULT_TARGETS,
        "workers_num": 1,
        "exit_first": False,
        "dry_run": False,
        "stateful": None,
        "stateful_recursion_limit": 5,
        "auth": None,
        "auth_type": "basic",
        "headers": {},
        "request_timeout": DEFAULT_RESPONSE_TIMEOUT,
        "request_tls_verify": True,
        "store_interactions": False,
        "seed": None,
        "max_response_time": None,
        **expected,
    }
    hypothesis_settings = expected.pop("hypothesis_settings", None)
    call_kwargs = execute.call_args[1]
    executed_hypothesis_settings = call_kwargs.pop("hypothesis_settings", None)
    if hypothesis_settings is not None:
        # Compare non-default Hypothesis settings as `hypothesis.settings` can't be compared
        assert executed_hypothesis_settings.show_changed() == hypothesis_settings.show_changed()
    assert call_kwargs == expected


@pytest.mark.parametrize(
    "args, expected",
    (
        (["--auth=test:test"], {"auth": ("test", "test"), "auth_type": "basic"}),
        (["--auth=test:test", "--auth-type=digest"], {"auth": ("test", "test"), "auth_type": "digest"}),
        (["--auth=test:test", "--auth-type=DIGEST"], {"auth": ("test", "test"), "auth_type": "digest"}),
        (["--header=Authorization:Bearer 123"], {"headers": {"Authorization": "Bearer 123"}}),
        (["--header=Authorization:  Bearer 123 "], {"headers": {"Authorization": "Bearer 123 "}}),
        (["--method=POST", "--method", "GET"], {"method": ("POST", "GET")}),
        (["--method=POST", "--auth=test:test"], {"auth": ("test", "test"), "auth_type": "basic", "method": ("POST",)}),
        (["--endpoint=users"], {"endpoint": ("users",)}),
        (["--tag=foo"], {"tag": ("foo",)}),
        (["--operation-id=getUser"], {"operation_id": ("getUser",)}),
        (["--base-url=https://example.com/api/v1test"], {"base_url": "https://example.com/api/v1test"}),
    ),
)
def test_load_schema_arguments(cli, mocker, args, expected):
    mocker.patch("schemathesis.runner.SingleThreadRunner.execute", autospec=True)
    load_schema = mocker.patch("schemathesis.cli.load_schema", autospec=True)

    result = cli.run(SCHEMA_URI, *args)
    expected = LoaderConfig(
        SCHEMA_URI,
        **{
            **{
                "app": None,
                "base_url": None,
                "auth": None,
                "auth_type": "basic",
                "endpoint": None,
                "headers": {},
                "data_generation_methods": [DataGenerationMethod.default()],
                "method": None,
                "tag": None,
                "operation_id": None,
                "validate_schema": True,
                "skip_deprecated_operations": False,
                "force_schema_version": None,
                "request_tls_verify": True,
            },
            **expected,
        },
    )

    assert load_schema.call_args[0][0] == expected


def test_load_schema_arguments_headers_to_loader_for_app(testdir, cli, mocker):
    from_wsgi = mocker.patch("schemathesis.specs.openapi.loaders.from_wsgi", autospec=True)

    module = testdir.make_importable_pyfile(
        location="""
        from test.apps.openapi._flask import create_app

        app = create_app()
        """
    )
    cli.run("/schema.yaml", "--app", f"{module.purebasename}:app", "-H", "Authorization: Bearer 123")

    assert from_wsgi.call_args[1]["headers"]["Authorization"] == "Bearer 123"


def test_all_checks(cli, mocker, swagger_20):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)
    result = cli.run(SCHEMA_URI, "--checks=all")
    assert execute.call_args[1]["checks"] == ALL_CHECKS


@pytest.mark.operations()
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
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.operations("success")
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_success(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK, result.stdout
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
    assert 0 <= time < 5


@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_with_errors(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "1. Received a response with 5xx status code: 500" in lines
    assert "Performed checks:" in lines
    assert "    not_a_server_error                    1 / 3 passed          FAILED " in lines
    assert "== 1 passed, 1 failed in " in lines[-1]


@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_only_failure(cli, cli_args, app_type, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    if app_type == "real":
        assert "Response payload: `500: Internal Server Error`" in lines
    else:
        assert "<h1>Internal Server Error</h1>" in lines
    assert "    not_a_server_error                    0 / 2 passed          FAILED " in lines
    assert "== 1 failed in " in lines[-1]


@pytest.mark.operations("upload_file")
def test_cli_binary_body(cli, schema_url, hypothesis_max_examples):
    result = cli.run(
        schema_url,
        "--hypothesis-suppress-health-check=filter_too_much",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout


@pytest.mark.operations()
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_empty(cli, cli_args, workers):
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "No checks were performed." in lines
    assert "= Empty test suite =" in lines[-1]


@pytest.mark.operations()
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_changed_base_url(cli, server, cli_args, workers):
    # When the CLI receives custom base URL
    base_url = f"http://127.0.0.1:{server['port']}/api"
    result = cli.run(*cli_args, "--base-url", base_url, f"--workers={workers}")
    # Then the base URL should be correctly displayed in the CLI output
    lines = result.stdout.strip().split("\n")
    assert lines[5] == f"Base URL: {base_url}"


@pytest.mark.parametrize(
    "url, message",
    (
        ("/doesnt_exist", "Schema was not found at http://127.0.0.1"),
        ("/failure", "Failed to load schema, code 500 was returned from http://127.0.0.1"),
    ),
)
@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_execute_missing_schema(cli, openapi3_base_url, url, message, workers):
    result = cli.run(f"{openapi3_base_url}{url}", f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert message in result.stdout


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_hypothesis_failed_event(cli, cli_args, workers):
    # When the Hypothesis deadline option is set manually, and it is smaller than the response time
    result = cli.run(*cli_args, "--hypothesis-deadline=20", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And the given operation should be displayed as an error
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow E")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10].split("\n")[0] in ("E.", ".E", "EE")
        # empty line after all tests progress output
        assert lines[11] == ""
    # And the proper error message should be displayed
    assert "DeadlineExceeded: API response time is too slow! " in result.stdout
    assert "which exceeds the deadline of 20.00ms" in result.stdout
    # And the CLI should not suggest showing full tracebacks to the user
    assert "Add this option to your command line parameters to see full tracebacks" not in result.stdout


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_connection_timeout(cli, server, schema_url, workers):
    # When connection timeout is specified in the CLI and the request fails because of it
    result = cli.run(schema_url, "--request-timeout=80", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And the given operation should be displayed as a failure
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow F")
        assert lines[11].startswith("GET /api/success .")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10].split("\n")[0] in ("F.", ".F")
    # And the proper error message should be displayed
    assert "1. Response timed out after 80.00ms" in result.stdout


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_default_hypothesis_settings(cli, cli_args, workers):
    # When there is a slow operation and if it is faster than 15s
    result = cli.run(*cli_args, f"--workers={workers}")
    # Then the tests should pass, because of default 15s deadline
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow .")
        assert lines[11].startswith("GET /api/success .")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[10] == ".."


@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_seed(cli, cli_args, workers):
    # When there is a failure
    result = cli.run(*cli_args, "--hypothesis-seed=456", f"--workers={workers}")
    # Then the tests should fail and RNG seed should be displayed
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "Or add this option to your command line parameters: --hypothesis-seed=456" in result.stdout.split("\n")


@pytest.mark.operations("unsatisfiable")
@pytest.mark.parametrize("workers", (1, 2))
def test_unsatisfiable(cli, cli_args, workers):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters definition - requires to be integer AND string at the
    # same time
    result = cli.run(*cli_args, f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this operation should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/unsatisfiable E")
    else:
        assert lines[10] == "E"
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Unsatisfiable: Unable to satisfy schema parameters for this API operation" in lines


@pytest.mark.operations("flaky")
@pytest.mark.parametrize("workers", (1, 2))
def test_flaky(cli, cli_args, workers):
    # When the operation fails / succeeds randomly
    # Derandomize is needed for reproducible test results
    result = cli.run(*cli_args, "--hypothesis-derandomize", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And standard Hypothesis error should not appear in the output
    assert "Failed to reproduce exception. Expected:" not in result.stdout
    # And this operation should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/flaky E")
    else:
        assert lines[10] == "E"
    # And it should be displayed only once in "ERRORS" section
    assert "= ERRORS =" in result.stdout
    assert "_ GET /api/flaky [P] _" in result.stdout
    # And it should not go into "FAILURES" section
    assert "= FAILURES =" not in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert "hypothesis.errors.Flaky: Tests on this API operation produce unreliable results: " in lines
    assert "Falsified on the first call but did not on a subsequent one" in lines
    # And example is displayed
    assert "Query           : {'id': 0}" in lines


@pytest.mark.operations("invalid")
@pytest.mark.parametrize("workers", (1,))
def test_invalid_operation(cli, cli_args, workers):
    # When the app's schema contains errors
    # For example if its type is "int" but should be "integer"
    # And schema validation is disabled
    result = cli.run(*cli_args, f"--workers={workers}", "--validate-schema=false")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And standard Hypothesis error should not appear in the output
    assert "You can add @seed" not in result.stdout
    # And this operation should be marked as errored in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/invalid E")
    else:
        assert lines[10] == "E"
    assert " POST /api/invalid " in lines[13]
    # There shouldn't be a section end immediately after section start - there should be some error text
    # An internal error happened during a test run
    # Error: AssertionError
    assert not lines[14].startswith("=")


@pytest.mark.operations("invalid")
def test_invalid_operation_suggestion(cli, cli_args):
    # When the app's schema contains errors
    result = cli.run(*cli_args)
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And there should be a suggestion to disable schema validation
    expected = """You can disable input schema validation with --validate-schema=false command-line option
In this case, Schemathesis cannot guarantee proper behavior during the test run
"""
    assert expected in result.stdout


@pytest.mark.operations("invalid")
def test_invalid_operation_suggestion_disabled(cli, cli_args):
    # When the app's schema contains errors
    # And schema validation is disabled
    result = cli.run(*cli_args, "--validate-schema=false")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And there should be no suggestion
    assert "You can disable input schema validation" not in result.stdout


@pytest.mark.operations("teapot")
@pytest.mark.parametrize("workers", (1, 2))
def test_status_code_conformance(cli, cli_args, workers):
    # When operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    result = cli.run(*cli_args, "-c", "status_code_conformance", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And this operation should be marked as failed in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("POST /api/teapot F")
    else:
        assert lines[10] == "F"
    assert "status_code_conformance                    0 / 2 passed          FAILED" in result.stdout
    lines = result.stdout.split("\n")
    assert "1. Received a response with a status code, which is not defined in the schema: 418" in lines
    assert lines[16].strip() == "Declared status codes: 200"


@pytest.mark.operations("headers")
def test_headers_conformance_invalid(cli, cli_args):
    result = cli.run(*cli_args, "-c", "response_headers_conformance")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.split("\n")
    assert "1. Received a response with missing headers: X-Custom-Header" in lines


@pytest.mark.operations("headers")
def test_headers_conformance_valid(cli, cli_args):
    result = cli.run(*cli_args, "-c", "response_headers_conformance", "-H", "X-Custom-Header: bla")
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    assert "1. Received a response with missing headers: X-Custom-Header" not in lines


@pytest.mark.operations("multiple_failures")
def test_multiple_failures_single_check(cli, schema_url):
    result = cli.run(schema_url, "--hypothesis-seed=1", "--hypothesis-derandomize")

    assert "= HYPOTHESIS OUTPUT =" not in result.stdout
    assert "Hypothesis found 2 distinct failures" not in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "1. Received a response with 5xx status code: 500" in lines
    assert "2. Received a response with 5xx status code: 504" in lines
    assert "1 failed in " in lines[-1]


@pytest.mark.operations("multiple_failures")
def test_multiple_failures_different_check(cli, schema_url):
    result = cli.run(
        schema_url,
        "-c",
        "status_code_conformance",
        "-c",
        "not_a_server_error",
        "--hypothesis-derandomize",
        "--hypothesis-seed=1",
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
    result = cli.run(schema_url, "--base-url=http://127.0.0.1:1/api", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And all collected API operations should be marked as errored
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/failure E")
        assert lines[11].startswith("GET /api/success E")
    else:
        assert lines[10] == "EE"
    # And errors section title should be displayed
    assert "= ERRORS =" in result.stdout
    # And all API operations should be mentioned in this section as subsections
    assert "_ GET /api/success [P] _" in result.stdout
    assert "_ GET /api/failure [P] _" in result.stdout
    # And the proper error messages should be displayed for each operation
    assert "Max retries exceeded with url: /api/success" in result.stdout
    assert "Max retries exceeded with url: /api/failure" in result.stdout


@pytest.mark.parametrize("workers", (1, 2))
def test_schema_not_available(cli, workers):
    # When the given schema is unreachable
    result = cli.run("http://127.0.0.1:1/schema.yaml", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And error message is displayed
    lines = result.stdout.split("\n")
    assert lines[0] == "Failed to load schema from http://127.0.0.1:1/schema.yaml"
    assert lines[1].startswith(
        "Error: requests.exceptions.ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=1): "
        "Max retries exceeded with url: /schema.yaml"
    )


def test_schema_not_available_wsgi(cli, loadable_flask_app):
    # When the given schema is unreachable
    result = cli.run("unknown.yaml", f"--app={loadable_flask_app}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And error message is displayed
    lines = result.stdout.split("\n")
    assert lines[0] == "Schema was not found at unknown.yaml"


@pytest.mark.operations("custom_format")
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
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all registered new string format should produce digits as expected
    assert all(request.query["id"].isdigit() for request in app["incoming_requests"])


def test_pre_run_hook_invalid(testdir, cli):
    # When `--pre-run` hook is passed to the CLI call
    # And its importing causes an exception
    module = testdir.make_importable_pyfile(hook="1 / 0")

    result = cli.main("--pre-run", module.purebasename, "run", "http://127.0.0.1:1")

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And a helpful message should be displayed in the output
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "An exception happened during the hook loading:"
    assert lines[7] == "ZeroDivisionError: division by zero"
    assert lines[9] == "Aborted!"


def test_pre_run_hook_module_not_found(testdir, cli):
    testdir.makepyfile(hook="1 / 0")
    result = cli.main("--pre-run", "hook", "run", "http://127.0.0.1:1")

    assert os.getcwd() in sys.path

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "ModuleNotFoundError" not in result.stdout
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "An exception happened during the hook loading:"
    assert lines[7] == "ZeroDivisionError: division by zero"
    assert lines[9] == "Aborted!"


@pytest.mark.usefixtures("reset_hooks")
def test_conditional_checks(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.register_check
            def conditional_check(response, case):
                # skip this check
                return True
            """
    )

    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        "-c",
        "conditional_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )

    assert result.exit_code == ExitCode.OK
    # One additional case created for two API operations - /api/failure and /api/success.
    assert "No checks were performed." in result.stdout


@pytest.mark.usefixtures("reset_hooks")
def test_add_case(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hooks.register
            def add_case(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["copy"] = "this is a copied case"
                return case

            @schemathesis.register_check
            def add_case_check(response, case):
                if case.headers and case.headers.get("copy") == "this is a copied case":
                    # we will look for this output
                    click.echo("The case was added!")
            """
    )

    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )

    assert result.exit_code == ExitCode.OK
    # One additional case created for two API operations - /api/failure and /api/success.
    assert result.stdout.count("The case was added!") == 2


@pytest.mark.usefixtures("reset_hooks")
def test_add_case_returns_none(testdir, cli, hypothesis_max_examples, schema_url):
    """Tests that no additional test case created when the add_case hook returns None."""
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hooks.register
            def add_case(context, case, response):
                return None

            @schemathesis.register_check
            def add_case_check(response, case):
                click.echo("Validating case.")
            """
    )

    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )

    assert result.exit_code == ExitCode.OK
    # with --hypothesis-max-examples=1 and 2 API operations, only two cases should be created and validated.
    # If the count is greater than 2, additional test cases should not have been created but were created.
    assert result.stdout.count("Validating case.") == 2


@pytest.mark.usefixtures("reset_hooks")
def test_multiple_add_case_hooks(testdir, cli, hypothesis_max_examples, schema_url):
    """add_case hooks that mutate the case in place should not affect other cases."""

    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hooks.register("add_case")
            def add_first_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["first"] = "first header"
                return case

            @schemathesis.hooks.register("add_case")
            def add_second_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["second"] = "second header"
                return case

            @schemathesis.register_check
            def add_case_check(response, case):
                if case.headers and case.headers.get("first") == "first header":
                    # we will look for this output
                    click.echo("First case added!")
                if case.headers and case.headers.get("second") == "second header":
                    # we will look for this output
                    click.echo("Second case added!")
            """
    )

    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )

    assert result.exit_code == ExitCode.OK
    # Each header should only be duplicated once for each API operation - /api/failure and /api/success.
    assert result.stdout.count("First case added!") == 2
    assert result.stdout.count("Second case added!") == 2


@pytest.mark.usefixtures("reset_hooks")
def test_add_case_output(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hooks.register("add_case")
            def add_first_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["first"] = "first header"
                return case

            @schemathesis.hooks.register("add_case")
            def add_second_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["second"] = "second header"
                return case

            @schemathesis.register_check
            def add_case_check(response, case):
                if (
                    case.headers and
                    (
                        case.headers.get("second") == "second header"
                    )
                ):
                    assert False, "failing cases from second add_case hook"
            """
    )

    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
    )

    assert result.exit_code == ExitCode.TESTS_FAILED
    assert result.stdout.count("failing cases from second add_case hook") == 2
    add_case_check_line = next(
        filter(lambda line: line.strip().startswith("add_case_check"), result.stdout.split("\n"))
    )
    assert "8 / 12" in add_case_check_line


@pytest.fixture
def reset_hooks():
    yield
    unregister_all()
    reset_checks()


@pytest.fixture(
    params=[
        ('AssertionError("Custom check failed!")', "1. Custom check failed!"),
        ("AssertionError", "1. Check 'new_check' failed"),
    ]
)
def new_check(request, testdir, cli):
    exception, message = request.param
    module = testdir.make_importable_pyfile(
        hook=f"""
            import schemathesis

            @schemathesis.register_check
            def new_check(response, result):
                raise {exception}
            """
    )
    yield module, message
    reset_checks()
    # To verify that "new_check" is unregistered
    result = cli.run("--help")
    lines = result.stdout.splitlines()
    assert (
        "  -c, --checks [not_a_server_error|status_code_conformance|content_type_conformance|"
        "response_headers_conformance|response_schema_conformance|all]" in lines
    )


@pytest.mark.operations("success")
def test_register_check(new_check, cli, schema_url):
    new_check, message = new_check
    # When `--pre-run` hook is passed to the CLI call
    # And it contains registering a new check, which always fails for the testing purposes
    result = cli.main("--pre-run", new_check.purebasename, "run", "-c", "new_check", schema_url)

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And a message from the new check should be displayed
    lines = result.stdout.strip().split("\n")
    assert lines[14] == message


def assert_threaded_executor_interruption(lines, expected, optional_interrupt=False):
    # It is possible to have a case when first call without an error will start processing
    # But after, another thread will have interruption and will push this event before the
    # first thread will finish. Race condition: "" is for this case and "." for the other
    # way around
    # The app under test was killed ungracefully and since we run it in a child or the main thread
    # its output might occur in the captured stdout.
    ignored_exception = "Exception ignored in: " in lines[10]
    assert lines[10] in expected or ignored_exception, lines
    if not optional_interrupt:
        assert "!! KeyboardInterrupt !!" in lines[11], lines
    assert any("=== SUMMARY ===" in line for line in lines[10:])


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt(cli, cli_args, base_url, mocker, flask_app, swagger_20, workers):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    operation = APIOperation("/success", "GET", {}, swagger_20, base_url=base_url)
    if len(cli_args) == 2:
        operation.app = flask_app
        original = Case(operation).call_wsgi
    else:
        original = Case(operation).call
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
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then execution stops, and a message about interruption is displayed
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


@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt_threaded(cli, cli_args, mocker):
    # When a Schemathesis run is interrupted by the keyboard or via SIGINT
    original = time.sleep
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.runner.impl.threadpool.time.sleep", autospec=True, wraps=mocked)
    result = cli.run(*cli_args, "--workers=2", "--hypothesis-derandomize")
    # the exit status depends on what thread finished first
    assert result.exit_code in (ExitCode.OK, ExitCode.TESTS_FAILED), result.stdout
    # Then execution stops, and a message about interruption is displayed
    lines = result.stdout.strip().split("\n")
    # There are many scenarios possible, depends on how many tests will be executed before interruption
    # and in what order. it could be no tests at all, some of them or all of them.
    assert_threaded_executor_interruption(lines, ("F", ".", "F.", ".F", ""), True)


@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_hypothesis_output_capture(mocker, cli, cli_args, workers):
    mocker.patch("schemathesis.utils.IGNORED_PATTERNS", ())

    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "= HYPOTHESIS OUTPUT =" in result.stdout
    assert "Falsifying example" in result.stdout


async def test_multiple_files_schema(openapi_2_app, testdir, cli, hypothesis_max_examples, openapi2_base_url):
    # When the schema contains references to other files
    uri = pathlib.Path(HERE).as_uri() + "/"
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
                            # so specifying an absolute uri
                            "schema": {"$ref": urljoin(uri, "data/petstore_v2.yaml#/definitions/Pet")},
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
    openapi_2_app["config"].update({"should_fail": True, "schema_data": schema})
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    # And file path is given to the CLI
    result = cli.run(
        str(schema_file),
        f"--base-url={openapi2_base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--hypothesis-derandomize",
    )
    # Then Schemathesis should resolve it and run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all relevant requests should contain proper data for resolved references
    payload = await openapi_2_app["incoming_requests"][0].json()
    assert isinstance(payload["name"], str)
    assert isinstance(payload["photoUrls"], list)


def test_wsgi_app(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps.openapi._flask import create_app

        app = create_app()
        """
    )
    result = cli.run("/schema.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_exception(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps.openapi._flask import create_app

        1 / 0
        """
    )
    result = cli.run("/schema.yaml", "--app", f"{module.purebasename}:app", "--show-errors-tracebacks")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "Traceback (most recent call last):" in result.stdout
    assert "ZeroDivisionError: division by zero" in result.stdout


def test_wsgi_app_missing(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps.openapi._flask import create_app
        """
    )
    result = cli.run("/schema.yaml", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.strip().split("\n")
    assert "AttributeError: module 'location' has no attribute 'app'" in lines
    assert "Can not import application from the given module!" in lines


def test_wsgi_app_internal_exception(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from test.apps.openapi._flask import create_app

        app = create_app()
        app.config["internal_exception"] = True
        """
    )
    result = cli.run("/schema.yaml", "--app", f"{module.purebasename}:app", "--hypothesis-derandomize")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.strip().split("\n")
    assert "== APPLICATION LOGS ==" in lines[48], result.stdout.strip()
    assert "ERROR in app: Exception on /api/success [GET]" in lines[50]
    assert lines[66] == "ZeroDivisionError: division by zero"


@pytest.mark.parametrize("args", ((), ("--base-url",)))
def test_aiohttp_app(openapi_version, request, cli, loadable_aiohttp_app, args):
    # When a URL is passed together with app
    if args:
        args += (request.getfixturevalue("base_url"),)
    result = cli.run("/schema.yaml", "--app", loadable_aiohttp_app, *args)
    # Then the schema should be loaded from that URL
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_remote_schema(cli, schema_url, loadable_flask_app):
    # When a URL is passed together with app
    result = cli.run(schema_url, "--app", loadable_flask_app)
    # Then the schema should be loaded from that URL
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "1 passed, 1 failed in" in result.stdout


def test_wsgi_app_path_schema(cli, loadable_flask_app):
    # When an existing path to schema is passed together with app
    result = cli.run(SIMPLE_PATH, "--app", loadable_flask_app)
    # Then the schema should be loaded from that path
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "1 passed in" in result.stdout


def test_multipart_upload(testdir, tmp_path, hypothesis_max_examples, openapi3_base_url, cli):
    cassette_path = tmp_path / "output.yaml"
    # When requestBody has a binary field or an array of binary items
    responses = {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "paths": {
            "/property": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                }
                            }
                        },
                    },
                    "responses": responses,
                }
            },
            "/array": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "files": {"type": "array", "items": {"type": "string", "format": "binary"}}
                                    },
                                    "required": ["files"],
                                }
                            }
                        },
                    },
                    "responses": responses,
                }
            },
        },
        "servers": [{"url": "https://api.example.com/{basePath}", "variables": {"basePath": {"default": "v1"}}}],
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    result = cli.run(
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--show-errors-tracebacks",
        "--hypothesis-derandomize",
        f"--store-network-log={cassette_path}",
    )
    # Then it should be correctly sent to the server
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "= ERRORS =" not in result.stdout

    with cassette_path.open() as fd:
        cassette = yaml.safe_load(fd)

    def decode(idx):
        request = cassette["http_interactions"][idx]["request"]
        if "body" not in request:
            return None
        return base64.b64decode(request["body"]["base64_string"])

    first_decoded = decode(0)
    if first_decoded:
        assert b'Content-Disposition: form-data; name="files"; filename="files"\r\n' in first_decoded
    last_decoded = decode(-1)
    if last_decoded:
        assert b'Content-Disposition: form-data; name="file"; filename="file"\r\n' in last_decoded
    # NOTE, that the actual API operation is not checked in this test


@pytest.mark.operations("form")
def test_urlencoded_form(cli, cli_args):
    # When the API operation accepts application/x-www-form-urlencoded
    result = cli.run(*cli_args)
    # Then Schemathesis should generate appropriate payload
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.operations("success")
def test_targeted(mocker, cli, cli_args, workers):
    target = mocker.spy(hypothesis, "target")
    result = cli.run(*cli_args, f"--workers={workers}", "--target=response_time")
    assert result.exit_code == ExitCode.OK, result.stdout
    target.assert_called_with(mocker.ANY, label="response_time")


def test_chained_internal_exception(testdir, cli, hypothesis_max_examples, openapi3_base_url):
    # When schema contains an error that causes an internal error in `jsonschema`
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        # Response code should be a string
                        200: {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(raw_schema))
    result = cli.run(
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--show-errors-tracebacks",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.splitlines()
    assert "The above exception was the direct cause of the following exception:" in lines


@pytest.mark.parametrize(
    "options, expected",
    (
        (
            ("--skip-deprecated-operations",),
            "Collected API operations: 1",
        ),
        (
            (),
            "Collected API operations: 2",
        ),
    ),
)
def test_skip_deprecated_operations(testdir, cli, openapi3_base_url, options, expected):
    # When there are some deprecated API operations
    definition = {
        "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    }
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": definition,
                "post": {
                    "deprecated": True,
                    **definition,
                },
            }
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(raw_schema))
    result = cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--hypothesis-max-examples=1", *options)
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then only not deprecated API operations should be selected
    assert expected in result.stdout.splitlines()


@pytest.fixture()
def fast_api_fixup():
    yield
    fixups.uninstall()


@pytest.mark.parametrize("fixup", ("all", "fast_api"))
def test_fast_api_fixup(testdir, cli, base_url, fast_api_schema, hypothesis_max_examples, fast_api_fixup, fixup):
    # When schema contains Draft 7 definitions as ones from FastAPI may contain
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(fast_api_schema))
    result = cli.run(
        str(schema_file),
        f"--base-url={base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        f"--fixups={fixup}",
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.operations("success")
def test_colon_in_headers(cli, schema_url, app):
    header = "X-FOO"
    value = "bar:spam"
    result = cli.run(schema_url, f"--header={header}:{value}")
    assert result.exit_code == ExitCode.OK
    assert app["incoming_requests"][0].headers[header] == value


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_openapi_links(cli, cli_args, schema_url, hypothesis_max_examples):
    # When the schema contains Open API links or Swagger 2 extension for links
    # And these links are nested - API operations in these links contain links to another operations
    result = cli.run(
        *cli_args,
        f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
        "--hypothesis-seed=1",
        "--hypothesis-derandomize",
        "--hypothesis-deadline=None",
        "--show-errors-tracebacks",
        "--stateful=links",
    )
    lines = result.stdout.splitlines()
    # Note, it might fail if it uncovers the placed bug, which this version of stateful testing should not uncover
    # It is pretty rare and requires a high number for the `max_examples` setting. This version is staged for removal
    # Therefore it won't be fixed
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then these links should be tested
    # And lines with the results of these tests should be indented
    assert lines[11].startswith("    -> GET /api/users/{user_id} .")
    # And percentage should be adjusted appropriately
    assert lines[11].endswith("[ 50%]")
    assert lines[12].startswith("        -> PATCH /api/users/{user_id} .")
    assert lines[12].endswith("[ 60%]")
    assert lines[13].startswith("    -> PATCH /api/users/{user_id} .")
    assert lines[13].endswith("[ 66%]")


@pytest.mark.parametrize("recursion_limit, expected", ((1, "....."), (5, "......")))
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_openapi_links_multiple_threads(cli, cli_args, schema_url, recursion_limit, hypothesis_max_examples, expected):
    # When the schema contains Open API links or Swagger 2 extension for links
    # And these links are nested - API operations in these links contain links to another operations
    result = cli.run(
        *cli_args,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--hypothesis-derandomize",
        "--hypothesis-deadline=None",
        "--hypothesis-suppress-health-check=too_slow,filter_too_much",
        "--show-errors-tracebacks",
        "--stateful=links",
        f"--stateful-recursion-limit={recursion_limit}",
        "--workers=2",
    )
    lines = result.stdout.splitlines()
    assert result.exit_code == ExitCode.OK, result.stdout
    assert lines[10] == expected + "." if hypothesis_max_examples else expected


def test_get_request_with_body(testdir, cli, base_url, hypothesis_max_examples, schema_with_get_payload):
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema_with_get_payload))
    result = cli.run(
        str(schema_file),
        f"--base-url={base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--show-errors-tracebacks",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.splitlines()
    assert "InvalidSchema: Body parameters are defined for GET request." in lines


@pytest.mark.operations("slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_max_response_time_invalid(cli, server, schema_url, workers):
    # When maximum response time check is specified in the CLI and the request takes more time
    result = cli.run(schema_url, "--max-response-time=50", f"--workers={workers}")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And the given operation should be displayed as a failure
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/slow F")
    else:
        assert lines[10].startswith("F")
    # And the proper error message should be displayed
    assert "max_response_time                     0 / 2 passed          FAILED" in result.stdout
    assert "Response time exceeded the limit of 50 ms" in result.stdout


@pytest.mark.operations("slow")
def test_max_response_time_valid(cli, server, schema_url):
    # When maximum response time check is specified in the CLI and the request takes less time
    result = cli.run(schema_url, "--max-response-time=200")
    # Then no errors should occur
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.parametrize("header", ("Authorization", "authorization"))
@pytest.mark.operations()
def test_auth_and_authorization_header_are_disallowed(cli, schema_url, header, openapi_version):
    # When ``--auth`` is passed together with ``--header`` that sets the ``Authorization`` header
    result = cli.run(schema_url, "--auth=test:test", f"--header={header}:token123")
    # Then it causes a validation error
    assert result.exit_code == ExitCode.INTERRUPTED
    assert (
        "Invalid value: Passing `--auth` together with `--header` that sets `Authorization` is not allowed."
        in result.stdout
    )


@pytest.mark.parametrize("workers_num", (1, 2))
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("failure", "success")
def test_exit_first(cli, schema_url, openapi_version, workers_num, mocker):
    # When the `--exit-first` CLI option is passed
    # And a failure occurs
    stop_worker = mocker.spy(threadpool, "stop_worker")
    result = cli.run(schema_url, "--exitfirst", "-w", str(workers_num))
    # Then tests are failed
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    if workers_num == 1:
        lines = result.stdout.split("\n")
        # And the execution should stop on the first failure
        for idx, line in enumerate(lines):
            if line.startswith("GET /api/failure F"):
                assert line.endswith("[ 50%]")
                break
        else:
            pytest.fail("Line is not found")
        # the "FAILURES" sections goes after a new line, rather then continuing to the next operation
        next_line = lines[idx + 1]
        assert next_line == ""
        assert "FAILURES" in lines[idx + 2]
    else:
        stop_worker.assert_called()


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_base_url_not_required_for_dry_run(testdir, cli, openapi_version, empty_open_api_3_schema):
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(empty_open_api_3_schema))
    result = cli.run(str(schema_file), "--dry-run")
    assert result.exit_code == ExitCode.OK, result.stdout


def test_long_operation_output(testdir, empty_open_api_3_schema):
    # See GH-990
    # When there is a narrow screen
    # And the API schema contains an operation with a long name
    empty_open_api_3_schema["paths"] = {
        f"/{'a' * 100}": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        },
        f"/{'a' * 10}": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(empty_open_api_3_schema))
    result = testdir.run("schemathesis", "run", str(schema_file), "--dry-run")
    # Then this operation name should be truncated
    assert result.ret == ExitCode.OK
    assert "GET /aaaaaaaaaa .                                                         [ 50%]" in result.outlines
    assert "GET /aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa[...] . [100%]" in result.outlines


def test_reserved_characters_in_operation_name(testdir, empty_open_api_3_schema):
    # See GH-992
    # When an API operation name contains `:`
    empty_open_api_3_schema["paths"] = {
        "/foo:bar": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(empty_open_api_3_schema))
    result = testdir.run("schemathesis", "run", str(schema_file), "--dry-run")
    # Then this operation name should be displayed with the leading `/`
    assert result.ret == ExitCode.OK
    assert "GET /foo:bar .                                                            [100%]" in result.outlines


def test_error_during_example_generation(testdir, cli):
    # See GH-994
    # When the API schema is in YAML
    # And contains an unquoted value, that is casted to boolean
    # And it is behind references
    # And there are examples of another parameter
    content = """
swagger: "2.0"
basePath: /
info:
  description: Test
  title: Test
  version: 1.0
parameters:
  Bar:
    in: body
    name: payload
    required: true
    schema:
      properties:
        name:
          example: test
          type: string
      type: object
paths:
  /test:
    post:
      parameters:
        - $ref: "#/parameters/Bar"
        - in: query
          name: test
          type: string
      responses:
        "201":
          description: Ok
definitions:
  Foo:
    properties:
      bar:
        example: foo
        type: string
      # Should be quoted
      on:
        example: true
        type: boolean
    type: object
"""
    schema_file = testdir.makefile(".yaml", schema=content)
    result = cli.run(str(schema_file), "--dry-run", "--validate-schema=false")
    # Then the run should not be interrupted, but the run fails
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert " The API schema contains non-string keys" in result.stdout


def test_unsupported_regex(testdir, cli, empty_open_api_3_schema):
    def make_definition(min_items):
        return {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                # Java-style regular expression
                                "items": {"type": "string", "pattern": r"\p{Alpha}"},
                                "maxItems": 3,
                                "minItems": min_items,
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }

    # When an operation uses an unsupported regex syntax
    empty_open_api_3_schema["paths"] = {
        # Can't generate anything
        "/foo": make_definition(min_items=1),
        # Can generate an empty array
        "/bar": make_definition(min_items=0),
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(empty_open_api_3_schema))
    result = cli.run(str(schema_file), "--dry-run", "--hypothesis-max-examples=1")
    # Then if it is possible it should generate at least something
    assert "POST /bar ." in result.stdout
    # And if it is not then there should be an error with a descriptive error message
    assert "POST /foo E" in result.stdout
    lines = result.stdout.splitlines()
    for idx, line in enumerate(lines):
        if "__ POST /foo [P] __" in line:
            break
    else:
        pytest.fail("Line not found")
    assert r"Got pattern='\\p{Alpha}', but this is not valid syntax for a Python regular expression" in lines[idx + 1]


@pytest.mark.parametrize("extra", ("--auth='test:wrong'", "-H Authorization: Basic J3Rlc3Q6d3Jvbmcn"))
@pytest.mark.operations("basic")
def test_auth_override_on_protected_operation(cli, base_url, schema_url, extra):
    # See GH-792
    # When the tested API operation has basic auth
    # And the auth is overridden (directly or via headers)
    result = cli.run(schema_url, "--checks=all", extra)
    # And there is an error during testing
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.splitlines()
    # Then request representation in the output should have the overridden value
    assert (
        lines[18] == f"Headers         : {{'Authorization': 'Basic J3Rlc3Q6d3Jvbmcn', 'User-Agent': '{USER_AGENT}',"
        f" 'Accept-Encoding': 'gzip, deflate', 'Accept': '*/*', 'Connection': 'keep-alive'}}"
    )
    # And code sample as well
    assert (
        lines[26] == f"    requests.get('{base_url}/basic', headers={{'Authorization': 'Basic J3Rlc3Q6d3Jvbmcn', "
        f"'User-Agent': '{USER_AGENT}', 'Accept-Encoding': 'gzip, deflate', 'Accept': '*/*', "
        f"'Connection': 'keep-alive'}})"
    )


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("flaky")
def test_explicit_headers_in_output_on_errors(cli, base_url, schema_url, openapi_version):
    # When there is a non-fatal error during testing (e.g. flakiness)
    # And custom headers were passed explicitly
    auth = "Basic J3Rlc3Q6d3Jvbmcn"
    result = cli.run(schema_url, "--checks=all", f"-H Authorization: {auth}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    lines = result.stdout.splitlines()
    # Then request representation in the output should have the overridden value
    assert lines[17] == f"Headers         : {{'Authorization': '{auth}', 'User-Agent': '{USER_AGENT}'}}"
    # And code sample as well
    assert lines[22].startswith(
        f"    requests.get('{base_url}/flaky', headers={{'User-Agent': '{USER_AGENT}', "
        f"'Authorization': '{auth}'}}, params={{'id': "
    )


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("__all__")
def test_debug_output(tmp_path, cli, schema_url, openapi_version, hypothesis_max_examples):
    # When the `--debug-output-file` option is passed
    debug_file = tmp_path / "debug.jsonl"
    cassette_path = tmp_path / "output.yaml"
    result = cli.run(
        schema_url,
        f"--debug-output-file={debug_file}",
        "--validate-schema=false",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        f"--store-network-log={cassette_path}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then all underlying runner events should be stored as JSONL file
    assert debug_file.exists()
    with debug_file.open(encoding="utf-8") as fd:
        lines = fd.readlines()
    for line in lines:
        json.loads(line)
    # And statuses are encoded as strings
    assert list(json.loads(lines[-1])["total"]["not_a_server_error"]) == ["success", "total", "failure"]


@pytest.mark.operations("cp866")
def test_response_payload_encoding(cli, cli_args):
    # See GH-1073
    # When the "failed" response has non UTF-8 encoding
    result = cli.run(*cli_args, "--checks=all")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then it should be displayed according its actual encoding
    assert "Response payload: `Тест`" in result.stdout.splitlines()


@pytest.mark.operations("conformance")
def test_response_schema_conformance_deduplication(cli, cli_args):
    # See GH-907
    # When the "response_schema_conformance" check is present
    # And the app return different error messages caused by the same validator
    result = cli.run(*cli_args, "--checks=response_schema_conformance")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then the errors should be deduplicated
    assert result.stdout.count("Response payload: ") == 1


@pytest.mark.parametrize("kind", ("env_var", "arg"))
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("success")
def test_no_color(monkeypatch, cli, schema_url, kind):
    args = (schema_url,)
    if kind == "env_var":
        monkeypatch.setenv("NO_COLOR", "1")
    if kind == "arg":
        args += ("--no-color",)
    result = cli.run(*args, color=True)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "[1m" not in result.stdout


@pytest.mark.parametrize("graphql_path", ("/graphql", "/foo"))
def test_graphql_url(cli, graphql_url, graphql_path):
    # When the target API is GraphQL
    result = cli.run(graphql_url, "--hypothesis-max-examples=5")
    assert_graphql(result)


def test_graphql_asgi(cli, loadable_graphql_fastapi_app, graphql_path):
    # When the target API is GraphQL
    result = cli.run(f"--app={loadable_graphql_fastapi_app}", "--hypothesis-max-examples=5", graphql_path)
    assert_graphql(result)


def assert_graphql(result):
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should be detected automatically
    assert "Specification version: GraphQL" in result.stdout
    assert "getBooks . " in result.stdout
    assert "getAuthors . " in result.stdout


def assert_exit_code(event_stream, code):
    with pytest.raises(SystemExit) as exc:
        execute(
            event_stream,
            workers_num=1,
            show_errors_tracebacks=False,
            validate_schema=False,
            store_network_log=None,
            junit_xml=None,
            verbosity=0,
            code_sample_style=CodeSampleStyle.default(),
            debug_output_file=None,
            schemathesis_io_token=None,
            schemathesis_io_url=service.DEFAULT_URL,
        )
    assert exc.value.code == code


def test_cli_execute(swagger_20, capsys):
    event_stream = from_schema(swagger_20).execute()
    for _ in event_stream:
        pass
    assert_exit_code(event_stream, 1)
    assert capsys.readouterr().out.strip() == "Unexpected error"


def test_get_exit_code(swagger_20, capsys):
    event_stream = from_schema(swagger_20).execute()
    next(event_stream)
    event = next(event_stream)
    assert get_exit_code(event) == 1
