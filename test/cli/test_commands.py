import http.client
import json
import os
import pathlib
import platform
import sys
import time
from unittest.mock import ANY
from urllib.parse import urljoin
from xml.etree import ElementTree

import hypothesis
import pytest
import requests
import trustme
import urllib3.exceptions
import yaml
from _pytest.main import ExitCode
from aiohttp.test_utils import unused_port
from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

from schemathesis._dependency_versions import IS_PYTEST_ABOVE_7
from schemathesis.checks import ALL_CHECKS, DEFAULT_CHECKS, not_a_server_error
from schemathesis.cli import (
    DEPRECATED_PRE_RUN_OPTION_WARNING,
    LoaderConfig,
    execute,
    get_exit_code,
    reset_checks,
)
from schemathesis.cli.cassettes import CassetteFormat
from schemathesis.cli.constants import HealthCheck, Phase
from schemathesis.code_samples import CodeSampleStyle
from schemathesis.constants import (
    DEFAULT_DEADLINE,
    DEFAULT_RESPONSE_TIMEOUT,
    FLAKY_FAILURE_MESSAGE,
    REPORT_SUGGESTION_ENV_VAR,
)
from schemathesis.extra._flask import run_server
from schemathesis.generation import DataGenerationMethod, GenerationConfig
from schemathesis.internal.datetime import current_datetime
from schemathesis.internal.output import OutputConfig
from schemathesis.models import APIOperation, Case
from schemathesis.runner import CaseOverride, from_schema
from schemathesis.runner.impl import threadpool
from schemathesis.runner.probes import ProbeConfig
from schemathesis.specs.openapi import unregister_string_format
from schemathesis.specs.openapi.checks import status_code_conformance
from schemathesis.stateful import Stateful
from schemathesis.targets import DEFAULT_TARGETS
from schemathesis.transports import RequestConfig, WSGITransport
from test.apps._graphql._flask import create_app as create_graphql_app
from test.apps.openapi._flask import create_app as create_openapi_app
from test.utils import HERE, SIMPLE_PATH, flaky, strip_style_win32

PHASES = ", ".join(x.name for x in Phase)
HEALTH_CHECKS = "|".join(x.name for x in HealthCheck)


def test_commands_help(cli, snapshot_cli):
    assert cli.main() == snapshot_cli


def test_run_subprocess(testdir):
    # To verify that CLI entry point is installed properly
    result = testdir.run("schemathesis")
    assert result.ret == ExitCode.OK


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
def test_run_as_module(testdir):
    result = testdir.run("python", "-m", "schemathesis.cli")
    assert result.ret == ExitCode.OK


@pytest.mark.parametrize(
    "args",
    (
        (),
        (SIMPLE_PATH,),
        (SIMPLE_PATH, "--base-url=test"),
        (SIMPLE_PATH, "--base-url=127.0.0.1:8080"),
        ("http://127.0.0.1", "--request-timeout=-5"),
        ("http://127.0.0.1", "--request-timeout=0"),
        ("http://127.0.0.1", "--method=+"),
        ("http://127.0.0.1", "--auth=123"),
        ("http://127.0.0.1", "--auth=:pass"),
        ("http://127.0.0.1", "--auth=тест:pass"),
        ("http://127.0.0.1", "--auth=user:тест"),
        ("http://127.0.0.1", "--auth-type=random"),
        ("http://127.0.0.1", "--header=123"),
        ("http://127.0.0.1", "--header=:"),
        ("http://127.0.0.1", "--header= :"),
        ("http://127.0.0.1", "--header=тест:test"),
        ("http://127.0.0.1", "--header=test:тест"),
        ("http://127.0.0.1", "--hypothesis-phases=explicit,first,second"),
        ("http://127.0.0.1", "--hypothesis-deadline=wrong"),
        ("http://127.0.0.1", "--hypothesis-deadline=0"),
        ("//test",),
        ("http://127.0.0.1", "--max-response-time=0"),
        ("unknown.json",),
        ("unknown.json", "--base-url=http://127.0.0.1"),
        ("--help",),
        ("http://127.0.0.1", "--generation-codec=foobar"),
        ("http://127.0.0.1", "--set-query", "key=a\ud800b"),
        ("http://127.0.0.1", "--set-query", "key"),
        ("http://127.0.0.1", "--set-query", "=v"),
        ("http://127.0.0.1", "--set-header", "Token=тест"),
        ("http://127.0.0.1", "--set-cookie", "SESSION_ID=тест"),
        ("http://127.0.0.1", "--set-path", "user_id=\ud800b"),
        ("http://127.0.0.1", "--set-query", "key=value", "--set-query", "key=value"),
        ("http://127.0.0.1", "--set-header", "Authorization=value", "--auth", "foo:bar"),
        ("http://127.0.0.1", "--set-header", "Authorization=value", "-H", "Authorization: value"),
        ("http://127.0.0.1", "--hypothesis-no-phases=unknown"),
        ("http://127.0.0.1", "--hypothesis-no-phases=explicit", "--hypothesis-phases=explicit"),
        ("http://127.0.0.1", "--cassette-format=unknown"),
    ),
)
def test_run_output(cli, args, snapshot_cli):
    assert cli.run(*args) == snapshot_cli


def test_hooks_module_not_found(cli, snapshot_cli):
    # When an unknown hook module is passed to CLI
    assert cli.main("run", "http://127.0.0.1:1", hooks="hook") == snapshot_cli
    assert os.getcwd() in sys.path


def test_hooks_with_inner_import_error(testdir, cli, snapshot_cli):
    # When the hook module itself raises an ImportError
    module = testdir.make_importable_pyfile(hook="import something_else")
    assert cli.main("run", "http://127.0.0.1:1", hooks=module.purebasename) == snapshot_cli


def test_hooks_invalid(testdir, cli):
    # When hooks are passed to the CLI call
    # And its importing causes an exception
    module = testdir.make_importable_pyfile(hook="1 / 0")

    result = cli.main("run", "http://127.0.0.1:1", hooks=module.purebasename)

    # Then CLI run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And a helpful message should be displayed in the output
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "Unable to load Schemathesis extension hooks"
    if sys.version_info >= (3, 11):
        idx = 8
    else:
        idx = 7
    assert lines[idx] == "ZeroDivisionError: division by zero"


def test_certificate_only_key(cli, tmp_path, snapshot_cli):
    # When cert key is passed without cert itself
    # Then an appropriate error should be displayed
    assert cli.run("http://127.0.0.1", f"--request-cert-key={tmp_path}") == snapshot_cli


@pytest.mark.operations("invalid")
def test_invalid_operation_suggestion(cli, cli_args, snapshot_cli):
    # When the app's schema contains errors
    # Then the whole Schemathesis run should fail
    # And there should be a suggestion to disable schema validation
    assert cli.run(*cli_args, "--validate-schema=true") == snapshot_cli


@pytest.mark.operations("invalid")
def test_invalid_operation_suggestion_disabled(cli, cli_args):
    # When the app's schema contains errors
    # And schema validation is disabled
    result = cli.run(*cli_args, "--validate-schema=false")
    # Then the whole Schemathesis run should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # And there should be no suggestion
    assert "You can disable input schema validation" not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("header", ("Authorization", "authorization"))
def test_auth_and_authorization_header_are_disallowed(cli, schema_url, header, snapshot_cli):
    # When ``--auth`` is passed together with ``--header`` that sets the ``Authorization`` header
    # Then it causes a validation error
    assert cli.run(schema_url, "--auth=test:test", f"--header={header}:token123") == snapshot_cli


@pytest.mark.parametrize("workers", (1, 2))
def test_schema_not_available(cli, workers, snapshot_cli):
    # When the given schema is unreachable
    # Then the whole Schemathesis run should fail
    # And error message is displayed
    assert cli.run("http://127.0.0.1:1/schema.yaml", f"--workers={workers}") == snapshot_cli


def test_schema_not_available_wsgi(cli, loadable_flask_app, snapshot_cli):
    # When the given schema is unreachable
    # Then the whole Schemathesis run should fail
    # And error message is displayed
    assert cli.run("unknown.yaml", f"--app={loadable_flask_app}") == snapshot_cli


@pytest.mark.parametrize("args", ((), ("--force-schema-version=30",)))
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_empty_schema_file(testdir, cli, snapshot_cli, args):
    # When the schema file is empty
    filename = testdir.makefile(".json", schema="")
    # Then a proper error should be reported
    assert cli.run(str(filename), "--base-url=http://127.0.0.1:1", *args) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_force_color_nocolor(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--force-color", "--no-color") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_certificates(cli, schema_url, mocker):
    request = mocker.spy(requests.Session, "request")
    # When a cert is passed via CLI args
    ca = trustme.CA()
    cert = ca.issue_cert("test.org")
    with cert.private_key_pem.tempfile() as cert_path:
        result = cli.run(schema_url, f"--request-cert={cert_path}")
        assert result.exit_code == ExitCode.OK, result.stdout
        # Then both schema & test network calls should use this cert
        assert len(request.call_args_list) == 2
        assert request.call_args_list[0][1]["cert"] == request.call_args_list[1][1]["cert"] == str(cert_path)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_hypothesis_database_with_derandomize(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--hypothesis-database=:memory:", "--hypothesis-derandomize") == snapshot_cli


SCHEMA_URI = "https://example.schemathesis.io/openapi.json"


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
                    phases=[hypothesis.Phase.explicit, hypothesis.Phase.generate],
                    report_multiple_bugs=False,
                    suppress_health_check=[hypothesis.HealthCheck.too_slow, hypothesis.HealthCheck.filter_too_much],
                    verbosity=hypothesis.Verbosity.normal,
                )
            },
        ),
        (["--hypothesis-deadline=None"], {"hypothesis_settings": hypothesis.settings(deadline=None)}),
        (
            ["--hypothesis-no-phases=explicit"],
            {
                "hypothesis_settings": hypothesis.settings(
                    deadline=DEFAULT_DEADLINE,
                    phases=list(set(hypothesis.Phase) - {hypothesis.Phase.explicit, hypothesis.Phase.explain}),
                )
            },
        ),
        (["--max-response-time=10"], {"max_response_time": 10}),
    ),
)
def test_from_schema_arguments(cli, mocker, swagger_20, args, expected):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)

    cli.run(SCHEMA_URI, *args)

    expected = {
        "checks": DEFAULT_CHECKS,
        "targets": DEFAULT_TARGETS,
        "workers_num": 1,
        "exit_first": False,
        "max_failures": None,
        "started_at": ANY,
        "dry_run": False,
        "stateful": Stateful.links,
        "stateful_recursion_limit": 5,
        "auth": None,
        "auth_type": "basic",
        "override": CaseOverride({}, {}, {}, {}),
        "headers": {},
        "request_timeout": DEFAULT_RESPONSE_TIMEOUT,
        "request_tls_verify": True,
        "request_proxy": None,
        "request_cert": None,
        "store_interactions": False,
        "seed": None,
        "max_response_time": None,
        "generation_config": GenerationConfig(),
        "probe_config": ProbeConfig(auth_type="basic", headers={}, request=RequestConfig(timeout=10000)),
        "service_client": None,
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
        (
            ["--method=POST", "--auth=test:test"],
            {"auth": ("test", "test"), "auth_type": "basic"},
        ),
        (["--base-url=https://example.com/api/v1test"], {"base_url": "https://example.com/api/v1test"}),
    ),
)
def test_load_schema_arguments(cli, mocker, args, expected):
    mocker.patch("schemathesis.runner.impl.SingleThreadRunner.execute", autospec=True)
    load_schema = mocker.patch("schemathesis.cli.load_schema", autospec=True)

    cli.run(SCHEMA_URI, *args)
    expected = LoaderConfig(
        SCHEMA_URI,
        **{
            **{
                "app": None,
                "base_url": None,
                "wait_for_schema": None,
                "rate_limit": None,
                "auth": None,
                "auth_type": "basic",
                "headers": {},
                "data_generation_methods": [DataGenerationMethod.default()],
                "validate_schema": False,
                "output_config": OutputConfig(),
                "generation_config": GenerationConfig(),
                "force_schema_version": None,
                "request_tls_verify": True,
                "request_proxy": None,
                "request_cert": None,
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


@pytest.mark.parametrize(
    "factory, cls",
    (
        (lambda r: None, DirectoryBasedExampleDatabase),
        (lambda r: "none", type(None)),
        (lambda r: ":memory:", InMemoryExampleDatabase),
        (lambda r: r.getfixturevalue("tmpdir"), DirectoryBasedExampleDatabase),
    ),
)
def test_hypothesis_database_parsing(request, cli, mocker, swagger_20, factory, cls):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)
    database = factory(request)
    if database:
        args = (f"--hypothesis-database={database}",)
    else:
        args = ()
    cli.run(SCHEMA_URI, *args)
    hypothesis_settings = execute.call_args[1]["hypothesis_settings"]
    assert isinstance(hypothesis_settings.database, cls)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_hypothesis_database_report(cli, schema_url):
    result = cli.run(schema_url, "--hypothesis-database=:memory:", "-v")
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    assert lines[3] == "Hypothesis: database=InMemoryExampleDatabase({}), deadline=timedelta(milliseconds=15000)"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_metadata(cli, schema_url):
    # When the verbose mode is enabled
    result = cli.run(schema_url, "-v")
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    # Then there should be metadata displayed
    assert lines[1].startswith("platform")
    assert lines[2].startswith("rootdir")
    assert lines[3].startswith("Hypothesis")


def test_all_checks(cli, mocker, swagger_20):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)
    cli.run(SCHEMA_URI, "--checks=all")
    assert execute.call_args[1]["checks"] == ALL_CHECKS


def test_comma_separated_checks(cli, mocker, swagger_20):
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)
    cli.run(SCHEMA_URI, "--checks=not_a_server_error,status_code_conformance")
    assert execute.call_args[1]["checks"] == (not_a_server_error, status_code_conformance)


def test_comma_separated_exclude_checks(cli, mocker, swagger_20):
    excluded_checks = "not_a_server_error,status_code_conformance"
    mocker.patch("schemathesis.cli.load_schema", return_value=swagger_20)
    execute = mocker.patch("schemathesis.runner.from_schema", autospec=True)
    cli.run(SCHEMA_URI, "--checks=all", f"--exclude-checks={excluded_checks}")
    assert execute.call_args[1]["checks"] == tuple(
        check for check in tuple(ALL_CHECKS) if check.__name__ not in excluded_checks.split(",")
    )


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
        "--hypothesis-suppress-health-check=all",
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
    assert lines[5] == f"Workers: {workers}"
    if workers == 1:
        assert lines[11].startswith("GET /api/success .")
    else:
        assert lines[11] == "."
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    last_line = lines[-1]
    assert "== 1 passed in " in last_line
    # And the running time is a small positive number
    time = float(last_line.split(" ")[-2].replace("s", ""))
    assert 0 <= time < 5


@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_only_failure(cli, cli_args, workers, snapshot_cli):
    assert cli.run(*cli_args, f"--workers={workers}") == snapshot_cli


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
    assert lines[2] == f"Base URL: {base_url}"


@pytest.mark.parametrize(
    "url, message",
    (
        ("/doesnt_exist", "Failed to load schema due to client error (HTTP 404 Not Found)"),
        ("/failure", "Failed to load schema due to server error (HTTP 500 Internal Server Error)"),
    ),
)
@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", (1, 2))
def test_execute_missing_schema(cli, openapi3_base_url, url, message, workers):
    result = cli.run(f"{openapi3_base_url}{url}", f"--workers={workers}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert message in result.stdout


@flaky(max_runs=3, min_passes=1)
@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.snapshot(replace_multi_worker_progress="??", replace_statistic=True)
def test_hypothesis_failed_event(cli, cli_args, workers, snapshot_cli):
    # When the Hypothesis deadline option is set manually, and it is smaller than the response time
    # Then the whole Schemathesis run should fail
    # And the proper error message should be displayed
    assert cli.run(*cli_args, "--hypothesis-deadline=20", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_connection_timeout(cli, schema_url, workers, snapshot_cli):
    # When connection timeout is specified in the CLI and the request fails because of it
    # Then the whole Schemathesis run should fail
    # And the given operation should be displayed as a failure
    assert cli.run(schema_url, "--request-timeout=80", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_read_content_timeout(cli, mocker, schema_url, snapshot_cli):
    original = urllib3.response.HTTPResponse.stream
    count = 0

    def stream(self, *args, **kwargs):
        nonlocal count

        count += 1
        if count > 1:
            raise urllib3.exceptions.ReadTimeoutError(self._pool, None, "Read timed out.")
        return original(self, *args, **kwargs)

    mocker.patch("urllib3.response.HTTPResponse.stream", stream)
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_default_hypothesis_settings(cli, cli_args, workers):
    # When there is a slow operation and if it is faster than 15s
    result = cli.run(*cli_args, f"--workers={workers}")
    # Then the tests should pass, because of default 15s deadline
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[11].startswith("GET /api/slow .")
        assert lines[12].startswith("GET /api/success .")
    else:
        # It could be in any sequence, because of multiple threads
        assert lines[11] == ".."


@pytest.mark.operations("unsatisfiable")
@pytest.mark.parametrize("workers", (1, 2))
def test_unsatisfiable(cli, cli_args, workers, snapshot_cli):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters' definition - requires to be integer AND string at the
    # same time
    # And more clear error message is displayed instead of Hypothesis one
    assert cli.run(*cli_args, f"--workers={workers}") == snapshot_cli


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
    # And this operation should be marked as failed in the progress line
    lines = result.stdout.split("\n")
    if workers == 1:
        assert lines[10].startswith("GET /api/flaky F")
    else:
        assert lines[10] == "F"
    # And it should be displayed only once in "FAILURES" section
    assert "= FAILURES =" in result.stdout
    assert "_ GET /api/flaky _" in result.stdout
    # And more clear error message is displayed instead of Hypothesis one
    lines = result.stdout.split("\n")
    assert FLAKY_FAILURE_MESSAGE in lines


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
    assert lines[11].startswith("POST /api/invalid E")
    assert " POST /api/invalid " in lines[14]
    # There shouldn't be a section end immediately after section start - there should be error text
    assert (
        """Invalid definition for element at index 0 in `parameters`

Location:
    paths -> /invalid -> post -> parameters -> 0

Problematic definition:
"""
        in result.stdout
    )


@pytest.mark.operations("teapot")
@pytest.mark.parametrize("workers", (1, 2))
def test_status_code_conformance(cli, cli_args, workers, snapshot_cli):
    # When operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    # Then the whole Schemathesis run should fail
    # And this operation should be marked as failed in the progress line
    assert cli.run(*cli_args, "-c", "status_code_conformance", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("headers")
def test_headers_conformance_valid(cli, cli_args):
    result = cli.run(*cli_args, "-c", "response_headers_conformance", "-H", "X-Custom-Header: 42")
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    assert "1. Received a response with missing headers: X-Custom-Header" not in lines


@pytest.mark.operations("multiple_failures")
@pytest.mark.snapshot(replace_statistic=True)
def test_multiple_failures_single_check(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--hypothesis-seed=1", "--hypothesis-derandomize") == snapshot_cli


@pytest.mark.operations("multiple_failures")
@pytest.mark.snapshot(replace_statistic=True)
def test_multiple_failures_different_check(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "-c",
            "status_code_conformance",
            "-c",
            "not_a_server_error",
            "--hypothesis-derandomize",
            "--hypothesis-seed=1",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("workers", (1, 2))
def test_connection_error(cli, schema_url, workers, snapshot_cli):
    # When the given base_url is unreachable
    # Then the whole Schemathesis run should fail
    # And the proper error messages should be displayed for each operation
    assert cli.run(schema_url, "--base-url=http://127.0.0.1:1/api", f"--workers={workers}") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_chunked_encoding_error(mocker, cli, schema_url, app, snapshot_cli):
    app["config"]["chunked"] = True

    def _update_chunk_length(response):
        value = b""
        try:
            int(value, 16)
        except ValueError as e:
            raise urllib3.exceptions.InvalidChunkLength(response, value) from e

    mocker.patch("urllib3.response.HTTPResponse._update_chunk_length", _update_chunk_length)
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_remote_disconnected_error(mocker, cli, schema_url, snapshot_cli):
    mocker.patch(
        "http.client.HTTPResponse.begin",
        side_effect=http.client.RemoteDisconnected("Remote end closed connection without response"),
    )
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--request-proxy=http://127.0.0.1") == snapshot_cli


@pytest.fixture
def digits_format(testdir):
    module = testdir.make_importable_pyfile(
        hook="""
    import string
    import schemathesis
    from hypothesis import strategies as st

    schemathesis.openapi.format(
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
    yield module
    unregister_string_format("digits")


@pytest.mark.parametrize(
    "prepare_args_kwargs",
    (
        lambda module: (("--pre-run", module.purebasename), {}),
        lambda module: ((), {"hooks": module.purebasename}),
    ),
)
@pytest.mark.operations("custom_format")
def test_hooks_valid(cli, schema_url, app, digits_format, prepare_args_kwargs):
    # When a hook is passed to the CLI call
    args, kwargs = prepare_args_kwargs(digits_format)
    result = cli.main(*args, "run", "--hypothesis-suppress-health-check=filter_too_much", schema_url, **kwargs)

    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all registered new string format should produce digits as expected
    assert all(request.query["id"].isdigit() for request in app["incoming_requests"])
    # And the `--pre-run` version raises a deprecation warning
    if args:
        assert DEPRECATED_PRE_RUN_OPTION_WARNING in result.stdout


def test_conditional_checks(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis

            @schemathesis.check
            def conditional_check(response, case):
                # skip this check
                return True
            """
    )

    result = cli.main(
        "run",
        "-c",
        "conditional_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        hooks=module.purebasename,
    )

    assert result.exit_code == ExitCode.OK
    # One additional case created for two API operations - /api/failure and /api/success.
    assert "No checks were performed." in result.stdout


def test_add_case(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hook
            def add_case(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["copy"] = "this is a copied case"
                return case

            @schemathesis.check
            def add_case_check(response, case):
                if case.headers and case.headers.get("copy") == "this is a copied case":
                    # we will look for this output
                    click.echo("The case was added!")
            """
    )

    result = cli.main(
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        hooks=module.purebasename,
    )

    assert result.exit_code == ExitCode.OK
    # One additional case created for two API operations - /api/failure and /api/success.
    assert result.stdout.count("The case was added!") == 2


def test_add_case_returns_none(testdir, cli, hypothesis_max_examples, schema_url):
    """Tests that no additional test case created when the add_case hook returns None."""
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hook
            def add_case(context, case, response):
                return None

            @schemathesis.check
            def add_case_check(response, case):
                click.echo("Validating case.")
            """
    )

    result = cli.main(
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        hooks=module.purebasename,
    )

    assert result.exit_code == ExitCode.OK
    # with --hypothesis-max-examples=1 and 2 API operations, only two cases should be created and validated.
    # If the count is greater than 2, additional test cases should not have been created but were created.
    assert result.stdout.count("Validating case.") == 2


def test_multiple_add_case_hooks(testdir, cli, hypothesis_max_examples, schema_url):
    """add_case hooks that mutate the case in place should not affect other cases."""
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hook("add_case")
            def add_first_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["first"] = "first header"
                return case

            @schemathesis.hook("add_case")
            def add_second_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["second"] = "second header"
                return case

            @schemathesis.check
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
        "run",
        "-c",
        "add_case_check",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        hooks=module.purebasename,
    )

    assert result.exit_code == ExitCode.OK
    # Each header should only be duplicated once for each API operation - /api/failure and /api/success.
    assert result.stdout.count("First case added!") == 2
    assert result.stdout.count("Second case added!") == 2


def test_add_case_output(testdir, cli, hypothesis_max_examples, schema_url, snapshot_cli):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis
            import click

            @schemathesis.hook("add_case")
            def add_first_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["first"] = "first header"
                return case

            @schemathesis.hook("add_case")
            def add_second_header(context, case, response):
                if not case.headers:
                    case.headers = {}
                case.headers["second"] = "second header"
                return case

            @schemathesis.check
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

    assert (
        cli.main(
            "run",
            "-c",
            "add_case_check",
            schema_url,
            f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )


@pytest.fixture(
    params=[
        'AssertionError("Custom check failed!")',
        "AssertionError",
    ]
)
def new_check(request, testdir, cli):
    exception = request.param
    module = testdir.make_importable_pyfile(
        hook=f"""
            import schemathesis

            @schemathesis.check
            def new_check(response, result):
                raise {exception}
            """
    )
    yield module
    reset_checks()
    # To verify that "new_check" is unregistered
    assert "new_check" not in cli.run("--help").stdout


@pytest.mark.operations("success")
def test_register_check(new_check, cli, schema_url, snapshot_cli):
    # When hooks are passed to the CLI call
    # And it contains registering a new check, which always fails for the testing purposes
    # Then CLI run should fail
    # And a message from the new check should be displayed
    assert cli.main("run", "-c", "new_check", schema_url, hooks=new_check.purebasename) == snapshot_cli


def assert_threaded_executor_interruption(lines, expected, optional_interrupt=False):
    # It is possible to have a case when first call without an error will start processing
    # But after, another thread will have interruption and will push this event before the
    # first thread will finish. Race condition: "" is for this case and "." for the other
    # way around
    # The app under test was killed ungracefully and since we run it in a child or the main thread
    # its output might occur in the captured stdout.
    ignored_exception = "Exception ignored in: " in lines[8]
    assert lines[10] in expected or ignored_exception, lines
    if not optional_interrupt:
        assert any("!! KeyboardInterrupt !!" in line for line in lines[10:]), lines
    assert any("=== SUMMARY ===" in line for line in lines[9:])


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt(cli, cli_args, base_url, mocker, flask_app, swagger_20, workers):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    operation = APIOperation("/success", "GET", {}, swagger_20, base_url=base_url)
    if len(cli_args) == 2:
        operation.app = flask_app
        operation.schema.transport = WSGITransport(operation.app)
    original = Case(operation, generation_time=0.0).call
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            # For threaded case it emulates SIGINT for the worker thread
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    result = cli.run(*cli_args, f"--workers={workers}")
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then execution stops, and a message about interruption is displayed
    lines = result.stdout.strip().split("\n")
    # And summary is still displayed in the end of the output
    if workers == 1:
        assert lines[11].startswith("GET /api/failure .")
        assert lines[11].endswith("[ 50%]")
        assert lines[12] == "GET /api/success "
        assert "!! KeyboardInterrupt !!" in lines[13]
        assert "== SUMMARY ==" in lines[15]
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
    result = cli.run("/schema.yaml", "--app", f"{module.purebasename}:app", "--show-trace")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "Traceback (most recent call last):" in result.stdout
    assert "ZeroDivisionError: division by zero" in result.stdout


def test_no_useless_traceback(testdir, cli, empty_open_api_3_schema, snapshot_cli):
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "post": {
                "parameters": [
                    {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "properties": {
                                    "region": {
                                        "nullable": True,
                                        "pattern": "^[\\w\\s\\-\\/\\pL,.#;:()']+$",
                                        "type": "string",
                                    },
                                },
                                "required": ["region"],
                                "type": "object",
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), "--show-trace", "--dry-run") == snapshot_cli


def test_invalid_yaml(testdir, cli, simple_openapi, snapshot_cli):
    schema = yaml.dump(simple_openapi)
    schema += "\x00"
    schema_file = testdir.makefile(".yaml", schema=schema)
    assert cli.run(str(schema_file), "--show-trace", "--dry-run") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(
    sys.version_info < (3, 11) or platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
def test_useful_traceback(testdir, cli, schema_url, snapshot_cli):
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis


@schemathesis.check
def with_error(response, case):
    1 / 0
"""
    )
    assert cli.main("run", schema_url, "-c", "with_error", "--show-trace", hooks=module.purebasename) == snapshot_cli


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
    assert "An error occurred while loading the application from 'location:app'." in lines


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
    if sys.version_info >= (3, 11):
        assert lines[66] == "ZeroDivisionError: division by zero"
    else:
        assert lines[61] == '    raise ZeroDivisionError("division by zero")'


@pytest.mark.parametrize("args", ((), ("--base-url",)))
def test_aiohttp_app(request, cli, loadable_aiohttp_app, args):
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


@pytest.mark.parametrize("media_type", ("multipart/form-data", "multipart/mixed", "multipart/*"))
def test_multipart_upload(testdir, tmp_path, hypothesis_max_examples, openapi3_base_url, cli, media_type):
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
                            media_type: {
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
                            media_type: {
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
        "--show-trace",
        "--hypothesis-derandomize",
        f"--cassette-path={cassette_path}",
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
        return request["body"]["string"].encode()

    first_decoded = decode(0)
    if first_decoded:
        assert b'Content-Disposition: form-data; name="files"; filename="files"\r\n' in first_decoded
    last_decoded = decode(-1)
    if last_decoded:
        assert b'Content-Disposition: form-data; name="file"; filename="file"\r\n' in last_decoded
    # NOTE, that the actual API operation is not checked in this test


@pytest.mark.openapi_version("3.0")
def test_no_schema_in_media_type(testdir, cli, base_url, snapshot_cli):
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "paths": {
            "/property": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": {}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(schema)
    assert (
        cli.run(
            str(schema_file),
            f"--base-url={base_url}",
            "--hypothesis-max-examples=1",
            "--show-trace",
            "--validate-schema=true",
        )
        == snapshot_cli
    )


def test_nested_binary_in_yaml(testdir, openapi3_base_url, cli, snapshot_cli, empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/property": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "*/*": {
                            "schema": {
                                "type": "object",
                                "properties": {"file": {"type": "string", "format": "binary"}},
                                "required": ["file"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                },
            }
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--hypothesis-max-examples=10") == snapshot_cli


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


@pytest.mark.parametrize(
    "options, expected",
    (
        (
            ("--exclude-deprecated",),
            "Collected API operations: 1",
        ),
        (
            (),
            "Collected API operations: 2",
        ),
    ),
)
def test_exclude_deprecated(testdir, cli, openapi3_base_url, options, expected):
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


@pytest.mark.openapi_version("3.0")
def test_deprecated_filters(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--endpoint=success") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_duplicated_filters(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--include-path=success", "--include-path=success") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_invalid_filter(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--include-by=fooo") == snapshot_cli


@pytest.mark.parametrize("value", ("--include-by=/x-property == 42", "--exclude-by=/x-property != 42"))
@pytest.mark.operations("upload_file", "custom_format")
@pytest.mark.openapi_version("3.0")
def test_filter_by(cli, schema_url, snapshot_cli, value):
    assert cli.run(schema_url, "--dry-run", "--hypothesis-max-examples=1", value) == snapshot_cli


@pytest.mark.parametrize("fixup", ("all", "fast_api"))
def test_fast_api_fixup(testdir, cli, base_url, fast_api_schema, hypothesis_max_examples, fixup):
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
@flaky(max_runs=5, min_passes=1)
@pytest.mark.snapshot(replace_statistic=True)
def test_openapi_links(cli, cli_args, hypothesis_max_examples, snapshot_cli):
    # When the schema contains Open API links or Swagger 2 extension for links
    # And these links are nested - API operations in these links contain links to another operations
    # Note, it might fail if it uncovers the placed bug, which this version of stateful testing should not uncover
    # It is pretty rare and requires a high number for the `max_examples` setting. This version is staged for removal
    # Therefore it won't be fixed
    # Then these links should be tested
    # And lines with the results of these tests should be indented
    # And percentage should be adjusted appropriately
    assert (
        cli.run(
            *cli_args,
            f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
            "--hypothesis-suppress-health-check=all",
            "--hypothesis-seed=1",
            "--hypothesis-derandomize",
            "--hypothesis-deadline=None",
            "--show-trace",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_statistic=True)
def test_stateful_explicit_examples_with_filters(cli, schema_url, snapshot_cli):
    # See GH-2376
    assert (
        cli.run(
            schema_url,
            "--hypothesis-phases=explicit",
            # Should include only a single link from `create_user` to `get_user`
            # And the link from `create_user` to `update_user` should be excluded
            "--include-name=POST /api/users/",
            "--include-name=GET /api/users/{user_id}",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True, replace_statistic=True)
@pytest.mark.parametrize("workers", (1, 2))
def test_new_stateful_runner(cli, schema_url, snapshot_cli, workers, tmp_path):
    debug = tmp_path / "debug.log"
    assert (
        cli.run(
            schema_url,
            "--experimental=stateful-test-runner",
            f"--debug-output-file={debug}",
            "--hypothesis-max-examples=80",
            "--report=file.tar.gz",
            "--exitfirst",
            f"--workers={workers}",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True, replace_statistic=True)
def test_new_stateful_runner_sanitization(cli, schema_url, tmp_path):
    cassette_path = tmp_path / "output.yaml"
    token = "secret"
    result = cli.run(
        schema_url,
        "--experimental=stateful-test-runner",
        "--experimental=stateful-only",
        "--hypothesis-max-examples=80",
        f"--header=Authorization: Bearer {token}",
        f"--cassette-path={cassette_path}",
        "--exitfirst",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True, replace_statistic=True)
def test_new_stateful_runner_max_failures(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--experimental=stateful-test-runner",
            "--hypothesis-max-examples=80",
            "--max-failures=2",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_new_stateful_runner_with_cassette(tmp_path, cli, schema_url):
    cassette_path = tmp_path / "output.yaml"
    cli.run(
        schema_url,
        "--experimental=stateful-test-runner",
        "--hypothesis-max-examples=40",
        "--report=file.tar.gz",
        "--exitfirst",
        f"--cassette-path={cassette_path}",
    )
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        cassette = yaml.safe_load(fd)
    assert len(cassette["http_interactions"]) >= 20
    for interaction in cassette["http_interactions"]:
        assert interaction["seed"] not in (None, "None")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_new_stateful_runner_junit(tmp_path, cli, schema_url):
    junit_path = tmp_path / "junit.xml"
    cli.run(
        schema_url,
        "--experimental=stateful-test-runner",
        "--experimental=stateful-only",
        "--hypothesis-max-examples=80",
        "--exitfirst",
        f"--junit-xml={junit_path}",
    )
    assert junit_path.exists()
    tree = ElementTree.parse(junit_path)
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert len(root) == 1
    assert len(root[0]) == 1
    assert root[0][0].attrib["name"] == "Stateful tests"
    assert len(root[0][0]) == 1
    assert root[0][0][0].tag == "failure"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True, replace_statistic=True)
def test_new_stateful_runner_stateful_only(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--experimental=stateful-test-runner",
            "--experimental=stateful-only",
            "--hypothesis-max-examples=80",
            "--exitfirst",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_new_stateful_runner_stateful_only_with_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--base-url=http://127.0.0.1:1/api",
            "--experimental=stateful-test-runner",
            "--experimental=stateful-only",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_new_stateful_runner_filtered_out(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--experimental=stateful-test-runner",
            "--hypothesis-max-examples=40",
            "--include-path=/api/success",
            "--exitfirst",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_new_stateful_runner_proxy_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--request-proxy=http://127.0.0.1",
            "--experimental=stateful-test-runner",
            "--experimental=stateful-only",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True, replace_statistic=True)
def test_new_stateful_runner_generation_config(cli, mocker, schema_url, snapshot_cli):
    from schemathesis.specs.openapi import _hypothesis

    mocked = mocker.spy(_hypothesis, "from_schema")
    assert (
        cli.run(
            schema_url,
            "--experimental=stateful-test-runner",
            "--experimental=stateful-only",
            "--hypothesis-max-examples=1",
            "--generation-allow-x00=false",
            "--generation-codec=ascii",
            "--generation-with-security-parameters=false",
        )
        == snapshot_cli
    )
    from_schema_kwargs = mocked.call_args_list[0].kwargs
    assert from_schema_kwargs["allow_x00"] is False
    assert from_schema_kwargs["codec"] == "ascii"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_new_stateful_runner_keyboard_interrupt(cli, mocker, schema_url, snapshot_cli):
    def mocked(*args, **kwargs):
        raise KeyboardInterrupt

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    assert cli.run(schema_url, "--experimental=stateful-test-runner", "--experimental=stateful-only") == snapshot_cli


@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_statistic=True)
def test_openapi_links_disabled(cli, schema_url, hypothesis_max_examples, snapshot_cli):
    # When the user disabled Open API links usage
    assert (
        cli.run(
            schema_url,
            f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
            "--hypothesis-seed=1",
            "--hypothesis-derandomize",
            "--hypothesis-deadline=None",
            "--hypothesis-suppress-health-check=all",
            "--show-trace",
            "--stateful=none",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user")
def test_unresolvable_links(cli, empty_open_api_3_schema, testdir, snapshot_cli, base_url):
    empty_open_api_3_schema["paths"] = {
        "/users/": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "first_name": {"type": "string", "minLength": 3},
                                    "last_name": {"type": "string", "minLength": 3},
                                },
                                "required": ["first_name", "last_name"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "OK",
                        "links": {
                            "next": {
                                "operationId": "get_user",
                                "parameters": {"user_id": "$response.body#/invalid_value"},
                            },
                            "update": {
                                "operationId": "update_user",
                                "parameters": {"user_id": "$response.body#/id"},
                                "requestBody": {"first_name": "foo", "last_name": "bar"},
                            },
                        },
                    }
                },
            },
        },
        "/users/{user_id}": {
            "parameters": [{"in": "path", "name": "user_id", "required": True, "schema": {"type": "string"}}],
            "get": {
                "operationId": "get_user",
                "responses": {
                    "200": {
                        "description": "OK",
                    }
                },
            },
            "patch": {
                "operationId": "update_user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "first_name": {"type": "string", "minLength": 3},
                                    "last_name": {"type": "string", "minLength": 3},
                                },
                                "required": ["first_name", "last_name"],
                                "additionalProperties": False,
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert (
        cli.run(
            str(schema_file),
            f"--base-url={base_url}",
            "--hypothesis-max-examples=1",
            "--show-trace",
            "--validate-schema=true",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("recursion_limit", (1, 5))
@pytest.mark.operations("create_user", "get_user", "update_user")
@flaky(max_runs=5, min_passes=1)
@pytest.mark.snapshot(replace_statistic=True)
def test_openapi_links_multiple_threads(cli, cli_args, recursion_limit, hypothesis_max_examples, snapshot_cli):
    # When the schema contains Open API links or Swagger 2 extension for links
    # And these links are nested - API operations in these links contain links to another operations
    assert (
        cli.run(
            *cli_args,
            f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
            "--hypothesis-seed=1",
            "--hypothesis-derandomize",
            "--hypothesis-deadline=None",
            "--hypothesis-suppress-health-check=all",
            "--show-trace",
            f"--stateful-recursion-limit={recursion_limit}",
            "--workers=2",
        )
        == snapshot_cli
    )


def test_get_request_with_body(testdir, cli, base_url, hypothesis_max_examples, schema_with_get_payload, snapshot_cli):
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema_with_get_payload))
    assert (
        cli.run(
            str(schema_file),
            f"--base-url={base_url}",
            f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
            "--validate-schema=true",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_yaml_parsing_of_floats(cli, testdir, base_url, snapshot_cli):
    schema = """info:
  description: Test
  title: Test
  version: 0.1.0
openapi: 3.0.2
paths:
  /test:
    get:
      parameters:
      - in: query
        name: q
        schema:
          pattern: 00:00:00.00
          type: string
      responses:
        '200':
          description: OK"""
    schema_file = testdir.makefile(".yaml", schema=schema)
    assert cli.run(str(schema_file), f"--base-url={base_url}", "--dry-run") == snapshot_cli


@pytest.mark.operations("slow")
@pytest.mark.parametrize("workers", (1, 2))
def test_max_response_time_invalid(cli, schema_url, workers, snapshot_cli):
    # When maximum response time check is specified in the CLI and the request takes more time
    # Then the whole Schemathesis run should fail
    # And the given operation should be displayed as a failure
    # And the proper error message should be displayed
    assert cli.run(schema_url, "--max-response-time=50", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("slow")
def test_max_response_time_valid(cli, schema_url):
    # When maximum response time check is specified in the CLI and the request takes less time
    result = cli.run(schema_url, "--max-response-time=200")
    # Then no errors should occur
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.parametrize("workers_num", (1, 2))
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "success")
def test_exit_first(cli, schema_url, workers_num, mocker):
    # When the `--exit-first` CLI option is passed
    # And a failure occurs
    stop_worker = mocker.spy(threadpool, "stop_worker")
    result = cli.run(schema_url, "--exitfirst", "-w", str(workers_num))
    # Then tests are failed
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    if workers_num == 1:
        lines = result.stdout.split("\n")
        # And the execution should stop on the first failure
        for idx, line in enumerate(lines):  # noqa: B007
            if line.startswith("GET /api/failure F"):
                assert line.endswith("[ 50%]")
                break
        else:
            pytest.fail("Line is not found")
        # the "FAILURES" sections goes after a new line, rather than continuing to the next operation
        next_line = lines[idx + 1]
        assert next_line == ""
        assert "FAILURES" in lines[idx + 2]
    else:
        stop_worker.assert_called()


@pytest.mark.openapi_version("3.0")
def test_base_url_not_required_for_dry_run(testdir, cli, empty_open_api_3_schema):
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


def test_unsupported_regex(testdir, cli, empty_open_api_3_schema, snapshot_cli):
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
    # Then if it is possible it should generate at least something
    # And if it is not then there should be an error with a descriptive error message
    assert cli.run(str(schema_file), "--dry-run", "--hypothesis-max-examples=1") == snapshot_cli


@pytest.mark.parametrize("extra", ("--auth='test:wrong'", "-H Authorization: Basic J3Rlc3Q6d3Jvbmcn"))
@pytest.mark.operations("basic")
@pytest.mark.snapshot(replace_statistic=True)
def test_auth_override_on_protected_operation(cli, schema_url, extra, snapshot_cli):
    # See GH-792
    # When the tested API operation has basic auth
    # And the auth is overridden (directly or via headers)
    # And there is an error during testing
    # Then the code sample representation in the output should have the overridden value
    assert cli.run(schema_url, "--checks=all", "--sanitize-output=false", extra) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("flaky")
@pytest.mark.snapshot(replace_statistic=True)
def test_explicit_headers_in_output_on_errors(cli, schema_url, snapshot_cli):
    # When there is a non-fatal error during testing (e.g. flakiness)
    # And custom headers were passed explicitly
    auth = "Basic J3Rlc3Q6d3Jvbmcn"
    # Then the code sample should have the overridden value
    assert cli.run(schema_url, "--checks=all", "--sanitize-output=false", f"-H Authorization: {auth}") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("__all__")
def test_debug_output(tmp_path, cli, schema_url, hypothesis_max_examples):
    # When the `--debug-output-file` option is passed
    debug_file = tmp_path / "debug.jsonl"
    cassette_path = tmp_path / "output.yaml"
    result = cli.run(
        schema_url,
        f"--debug-output-file={debug_file}",
        "--validate-schema=false",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        f"--cassette-path={cassette_path}",
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
def test_response_payload_encoding(cli, cli_args, snapshot_cli):
    # See GH-1073
    # When the "failed" response has non UTF-8 encoding
    # Then it should be displayed according its actual encoding
    assert cli.run(*cli_args, "--checks=all") == snapshot_cli


@pytest.mark.operations("conformance")
def test_response_schema_conformance_deduplication(cli, cli_args, snapshot_cli):
    # See GH-907
    # When the "response_schema_conformance" check is present
    # And the app return different error messages caused by the same validator
    # Then the errors should be deduplicated
    assert cli.run(*cli_args, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("malformed_json")
def test_malformed_json_deduplication(cli, cli_args, snapshot_cli):
    # See GH-1518
    # When responses are not JSON as expected and their content differ each time
    # Then the errors should be deduplicated
    assert cli.run(*cli_args, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.parametrize("kind", ("env_var", "arg"))
@pytest.mark.openapi_version("3.0")
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


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(platform.system() == "Windows", reason="ANSI colors are not properly supported in Windows tests")
def test_force_color(cli, schema_url):
    # Using `--force-color` adds ANSI escape codes forcefully
    result = cli.run(schema_url, "--force-color", color=False)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "[1m" in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        (),
        ("--checks", "all"),
    ),
)
@pytest.mark.parametrize("graphql_path", ("/graphql", "/foo"))
def test_graphql_url(cli, graphql_url, graphql_path, args, snapshot_cli):
    # When the target API is GraphQL
    assert cli.run(graphql_url, "--hypothesis-max-examples=5", *args) == snapshot_cli


def test_graphql_asgi(cli, loadable_graphql_fastapi_app, graphql_path, snapshot_cli):
    # When the target API is GraphQL
    assert cli.run(f"--app={loadable_graphql_fastapi_app}", "--hypothesis-max-examples=5", graphql_path) == snapshot_cli


def assert_exit_code(event_stream, code):
    with pytest.raises(SystemExit) as exc:
        execute(
            event_stream,
            ctx=None,
            hypothesis_settings=hypothesis.settings(),
            workers_num=1,
            rate_limit=None,
            show_trace=False,
            wait_for_schema=None,
            validate_schema=False,
            cassette_path=None,
            cassette_format=CassetteFormat.VCR,
            cassette_preserve_exact_body_bytes=False,
            junit_xml=None,
            verbosity=0,
            code_sample_style=CodeSampleStyle.default(),
            data_generation_methods=[DataGenerationMethod.default()],
            debug_output_file=None,
            host_data=None,
            client=None,
            api_name=None,
            location="http://127.0.0.1",
            base_url=None,
            started_at=current_datetime(),
            report=None,
            telemetry=False,
            sanitize_output=False,
            output_config=None,
        )
    assert exc.value.code == code


def test_cli_execute(swagger_20, capsys):
    event_stream = from_schema(swagger_20).execute()
    for _ in event_stream:
        pass
    assert_exit_code(event_stream, 1)
    assert capsys.readouterr().out.strip() == "Unexpected error"


def test_get_exit_code(swagger_20):
    event_stream = from_schema(swagger_20).execute()
    next(event_stream)
    event = next(event_stream)
    assert get_exit_code(event) == 1


@pytest.mark.parametrize("base_url", (None, "http://127.0.0.1/apiv2"))
@pytest.mark.parametrize("location", ("path", "query", "header", "cookie"))
def test_missing_content_and_schema(cli, base_url, tmp_path, testdir, empty_open_api_3_schema, location, snapshot_cli):
    debug_file = tmp_path / "debug.jsonl"
    # When an Open API 3 parameter is missing `schema` & `content`
    empty_open_api_3_schema["paths"] = {
        "/foo": {"get": {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}}
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    args = [
        str(schema_file),
        f"--debug-output-file={debug_file}",
        "--dry-run",
        "--validate-schema=false",
        "--hypothesis-max-examples=1",
    ]
    if base_url is not None:
        args.append(f"--base-url={base_url}")
    # Then CLI should show that this API operation errored
    # And show the proper message under its "ERRORS" section
    assert cli.run(*args) == snapshot_cli
    # And emitted Before / After event pairs have the same correlation ids
    with debug_file.open(encoding="utf-8") as fd:
        events = [json.loads(line) for line in fd]
    assert events[5]["correlation_id"] == events[6]["correlation_id"]
    # And they should have the same "verbose_name"
    assert events[5]["verbose_name"] == events[6]["verbose_name"]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure")
def test_explicit_query_token_sanitization(empty_open_api_3_schema, testdir, cli, snapshot_cli, base_url):
    empty_open_api_3_schema["paths"] = {
        "/failure": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    empty_open_api_3_schema["security"] = [{"api_key": []}]
    empty_open_api_3_schema["components"] = {
        "securitySchemes": {
            "api_key": {
                "type": "apiKey",
                "name": "token",
                "in": "query",
            },
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    token = "token=secret"
    result = cli.run(str(schema_file), "--set-query", token, f"--base-url={base_url}")
    assert result == snapshot_cli
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_skip_not_negated_tests(cli, schema_url):
    # See GH-1463
    # When an endpoint has no parameters to negate
    result = cli.run(schema_url, "-D", "negative")
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should be skipped
    lines = result.stdout.splitlines()
    assert "1 skipped in" in lines[-1]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_dont_skip_when_generation_is_possible(cli, schema_url):
    result = cli.run(schema_url, "-D", "all")
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.splitlines()
    assert "1 passed in" in lines[-1]


@pytest.mark.operations("failure")
def test_explicit_example_failure_output(testdir, cli, openapi3_base_url, snapshot_cli):
    # When an explicit example fails
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "paths": {
            "/failure": {
                "get": {
                    "parameters": [{"in": "query", "name": "key", "example": "foo", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--sanitize-output=false") == snapshot_cli


@pytest.mark.operations("success")
def test_skipped_on_no_explicit_examples(cli, openapi3_schema_url):
    # See GH-1323
    # When there are no explicit examples
    result = cli.run(openapi3_schema_url, "--hypothesis-phases=explicit")
    # Then tests should be marked as skipped
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "/api/success S" in result.stdout
    assert "1 skipped in" in result.stdout


@pytest.mark.operations("success")
def test_digest_auth(cli, openapi3_schema_url):
    # When a digest auth is used
    result = cli.run(openapi3_schema_url, "--auth='test:test'", "--auth-type=digest")
    # Then it should not cause any exceptions
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.operations("basic")
def test_warning_on_unauthorized(cli, openapi3_schema_url):
    # When endpoint returns only 401
    result = cli.run(openapi3_schema_url)
    # Then the output should contain a warning about it
    assert result.exit_code == ExitCode.OK, result.stdout
    assert (
        "WARNING: Most of the responses from `GET /api/basic` have a 401 status code. "
        "Did you specify proper API credentials?" in strip_style_win32(result.stdout)
    )


@flaky(max_runs=5, min_passes=1)
@pytest.mark.operations("payload")
def test_multiple_data_generation_methods(testdir, cli, openapi3_schema_url):
    # When multiple data generation methods are supplied in CLI
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis

note = print

@schemathesis.check
def data_generation_check(response, case):
    if case.data_generation_method:
        note("METHOD: {}".format(case.data_generation_method.name))
"""
    )
    result = cli.main(
        "run",
        "-c",
        "data_generation_check",
        "-c",
        "not_a_server_error",
        openapi3_schema_url,
        "--hypothesis-max-examples=25",
        "--hypothesis-suppress-health-check=all",
        "-D",
        "all",
        hooks=module.purebasename,
    )
    # Then there should be cases generated from different methods
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "METHOD: positive" in result.stdout
    assert "METHOD: negative" in result.stdout


@pytest.mark.operations("success", "failure")
def test_warning_on_all_not_found(cli, openapi3_schema_url, openapi3_base_url):
    # When all endpoints return 404
    result = cli.run(openapi3_schema_url, f"--base-url={openapi3_base_url}/v4/")
    # Then the output should contain a warning about it
    assert result.exit_code == ExitCode.OK, result.stdout
    assert (
        "WARNING: All API responses have a 404 status code. "
        "Did you specify the proper API location?" in strip_style_win32(result.stdout)
    )


@pytest.mark.parametrize(
    "schema_path, app_factory",
    (
        (
            (
                "schema.yaml",
                lambda: create_openapi_app(operations=("success",)),
            ),
            (
                "graphql",
                create_graphql_app,
            ),
        )
    ),
)
def test_wait_for_schema(cli, schema_path, app_factory):
    # When Schemathesis is asked to wait for API schema to become available
    app = app_factory()
    original_run = app.run

    def run_with_delay(*args, **kwargs):
        time.sleep(0.1)
        return original_run(*args, **kwargs)

    app.run = run_with_delay
    port = unused_port()
    schema_url = f"http://127.0.0.1:{port}/{schema_path}"
    run_server(app, port=port)
    result = cli.run(schema_url, "--wait-for-schema=1", "--hypothesis-max-examples=1")
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
def test_wait_for_schema_not_enough(cli, snapshot_cli):
    app = create_openapi_app(operations=("success",))
    original_run = app.run

    def run_with_delay(*args, **kwargs):
        time.sleep(2)
        return original_run(*args, **kwargs)

    app.run = run_with_delay
    port = unused_port()
    schema_url = f"http://127.0.0.1:{port}/schema.yaml"
    run_server(app, port=port)

    assert cli.run(schema_url, "--wait-for-schema=1", "--hypothesis-max-examples=1") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_rate_limit(cli, schema_url):
    result = cli.run(schema_url, "--rate-limit=1/s")
    lines = result.stdout.splitlines()
    assert lines[6] == "Rate limit: 1/s"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_disable_report_suggestion(monkeypatch, cli, schema_url):
    monkeypatch.setenv(REPORT_SUGGESTION_ENV_VAR, "no")
    result = cli.run(schema_url)
    assert "You can visualize" not in result.stdout


@pytest.mark.parametrize("version", ("3.0.2", "3.1.0"))
def test_invalid_schema_with_disabled_validation(
    testdir, cli, openapi_3_schema_with_invalid_security, version, snapshot_cli
):
    # When there is an error in the schema
    openapi_3_schema_with_invalid_security["openapi"] = version
    schema_file = testdir.make_openapi_schema_file(openapi_3_schema_with_invalid_security)
    # And the validation is disabled (default)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_file), "--dry-run", "--experimental=openapi-3.1") == snapshot_cli


def test_unresolvable_reference_with_disabled_validation(
    testdir, cli, open_api_3_schema_with_recoverable_errors, snapshot_cli
):
    # When there is an error in the schema
    schema_file = testdir.make_openapi_schema_file(open_api_3_schema_with_recoverable_errors)
    # And the validation is disabled (default)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_file), "--dry-run") == snapshot_cli


@pytest.mark.parametrize("value", ("true", "false"))
@pytest.mark.operations("failure")
def test_output_sanitization(cli, openapi2_schema_url, hypothesis_max_examples, value):
    auth = "secret-auth"
    result = cli.run(
        openapi2_schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        f"-H Authorization: {auth}",
        f"--sanitize-output={value}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    if value == "false":
        expected = f"curl -X GET -H 'Authorization: {auth}'"
    else:
        expected = "curl -X GET -H 'Authorization: [Filtered]'"
    assert expected in result.stdout


@pytest.mark.operations("success")
@flaky(max_runs=5, min_passes=1)
def test_multiple_failures_in_single_check(
    testdir, mocker, response_factory, cli, empty_open_api_3_schema, openapi3_base_url, snapshot_cli
):
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "get": {
                "responses": {
                    "default": {"description": "text", "content": {"application/json": {"schema": {"type": "integer"}}}}
                }
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    response = response_factory.requests(content_type=None, status_code=200)
    mocker.patch("requests.Session.request", return_value=response)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--checks=all") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_binary_payload(testdir, cli, empty_open_api_3_schema, snapshot_cli, openapi3_base_url):
    empty_open_api_3_schema["paths"] = {
        "/binary": {
            "get": {
                "responses": {
                    "default": {
                        "description": "text",
                        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                    }
                }
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--checks=all") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_long_payload(testdir, cli, empty_open_api_3_schema, snapshot_cli, openapi3_base_url):
    empty_open_api_3_schema["paths"] = {
        "/long": {
            "get": {
                "responses": {
                    "default": {"description": "text", "content": {"application/json": {"schema": {"type": "array"}}}}
                }
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--checks=all") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
@pytest.mark.skipif(not IS_PYTEST_ABOVE_7, reason="Multiple errors are not caught on older pytest versions")
def test_multiple_errors(testdir, cli, empty_open_api_3_schema, snapshot_cli):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/octet-stream": {
                            "examples": {
                                "first": {
                                    "value": "FIRST",
                                }
                            },
                            "schema": {"format": "binary", "type": "string"},
                        },
                        "application/zip": {
                            "examples": {
                                "second": {
                                    "value": "SECOND",
                                }
                            },
                            "schema": {"format": "binary", "type": "string"},
                        },
                    },
                    "required": True,
                },
                "responses": {"204": {"description": "Success."}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), "--base-url=http://127.0.0.1:1") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
@pytest.mark.skipif(not IS_PYTEST_ABOVE_7, reason="Multiple errors are not caught on older pytest versions")
def test_group_errors(testdir, cli, empty_open_api_3_schema, snapshot_cli):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/x-json-smile": {
                            "schema": {
                                "properties": {
                                    "user_id": {
                                        "example": 1,
                                        "type": "integer",
                                    },
                                },
                                "required": ["user_id"],
                            }
                        },
                        "text/csv": {
                            "schema": {
                                "properties": {
                                    "user_id": {
                                        "example": 1,
                                        "type": "integer",
                                    },
                                },
                                "required": ["user_id"],
                            }
                        },
                    }
                },
                "responses": {"204": {"description": "Success."}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), "--base-url=http://127.0.0.1:1") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_complex_urlencoded_example(testdir, cli, empty_open_api_3_schema, snapshot_cli, openapi3_base_url):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {
                        "invalid": {"schema": {"example": 1}},
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "example": [
                                    {"tag": "0", "timestamp": "2016-04-07T19:39:18Z", "url": "http://127.0.0.1:8001"},
                                    {"tag": "1", "url": "http://127.0.0.1:8002"},
                                    {
                                        "tag": "2",
                                        "timestamp": "2016-04-07T19:39:18Z",
                                        "url": "http://127.0.0.1:8003",
                                    },
                                ],
                                "items": {
                                    "properties": {
                                        "closest": {
                                            "enum": ["either", "after", "before"],
                                            "type": "string",
                                        },
                                        "tag": {
                                            "type": "string",
                                        },
                                        "timestamp": {
                                            "type": "string",
                                        },
                                        "url": {"type": "string"},
                                    },
                                    "required": ["url"],
                                    "type": "object",
                                },
                                "type": "array",
                            }
                        },
                    }
                },
                "responses": {"204": {"description": "Success."}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--hypothesis-phases=explicit") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("plain_text_body")
def test_custom_strings(testdir, cli, hypothesis_max_examples, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis

            @schemathesis.check
            def custom_strings(response, case):
                try:
                    case.body.encode("ascii")
                except Exception as exc:
                    raise AssertionError(str(exc))
                assert "\\x00" not in case.body
            """
    )

    result = cli.main(
        "run",
        "-c",
        "custom_strings",
        "--generation-allow-x00=false",
        "--generation-codec=ascii",
        schema_url,
        f"--hypothesis-max-examples={hypothesis_max_examples or 100}",
        hooks=module.purebasename,
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("path_variable", "custom_format")
def test_parameter_overrides(testdir, cli, schema_url):
    module = testdir.make_importable_pyfile(
        hook="""
            import schemathesis

            @schemathesis.check
            def verify_overrides(response, case):
                if "key" in case.operation.path_parameters:
                    assert case.path_parameters["key"] == "foo"
                    assert "id" not in (case.query or {}), "`id` is present"
                if "id" in case.operation.query:
                    assert case.query["id"] == "bar"
                    assert "key" not in (case.path_parameters or {}), "`key` is present"
            """
    )

    result = cli.main(
        "run",
        "-c",
        "verify_overrides",
        "--set-path",
        "key=foo",
        "--set-query",
        "id=bar",
        schema_url,
        hooks=module.purebasename,
    )
    assert result.exit_code == ExitCode.OK, result.stdout


def test_null_byte_in_header_probe(testdir, cli, empty_open_api_3_schema, snapshot_cli, openapi3_base_url):
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "get": {
                "parameters": [{"name": "X-KEY", "in": "header", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    module = testdir.make_importable_pyfile(
        hook=r"""
            import schemathesis

            @schemathesis.check
            def no_null_bytes(response, case):
                assert "\x00" not in case.headers["X-KEY"]
            """
    )
    assert (
        cli.main(
            "run",
            str(schema_file),
            "-c",
            "no_null_bytes",
            f"--base-url={openapi3_base_url}",
            "--hypothesis-max-examples=1",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_recursive_reference_error_message(
    cli, testdir, schema_with_recursive_references, openapi3_base_url, snapshot_cli
):
    schema_file = testdir.make_openapi_schema_file(schema_with_recursive_references)
    assert cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--show-trace") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("payload")
@pytest.mark.snapshot(replace_statistic=True)
def test_unknown_schema_error(mocker, schema_url, cli, snapshot_cli):
    # Some AssertionError may be caused by internal error in dependencies and can not be associated with a specific
    # schema issue
    mocker.patch("hypothesis_jsonschema._from_schema.canonicalish", side_effect=AssertionError("Something bad happen"))
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_custom_cli_option(testdir, cli, schema_url, snapshot_cli):
    module = testdir.make_importable_pyfile(
        hook=r"""
    from schemathesis import cli, runner


    group = cli.add_group("My custom group")
    group.add_option("--custom-counter", type=int)

    group = cli.add_group("Another group", index=-1)
    group.add_option("--custom-counter-2", type=int)

    def gen():
        yield "first"
        yield "second"


    @cli.handler()
    class EventCounter(cli.EventHandler):
        def __init__(self, *args, **params):
            self.counter = params["custom_counter"] or 0

        def handle_event(self, context, event) -> None:
            self.counter += 1
            if isinstance(event, runner.events.Initialized):
                context.add_initialization_line("Counter initialized!")
                context.add_initialization_line(gen())
            elif isinstance(event, runner.events.Finished):
                context.add_summary_line(
                    f"Counter: {self.counter}",
                )
                context.add_summary_line(gen())
"""
    )
    assert (
        cli.main(
            "run",
            schema_url,
            "--custom-counter=42",
            "--dry-run",
            "--hypothesis-max-examples=1",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )
