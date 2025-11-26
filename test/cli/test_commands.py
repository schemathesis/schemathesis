import http.client
import json
import os
import pathlib
import platform
import sys
import time
from http.client import RemoteDisconnected
from urllib.parse import urljoin

import hypothesis
import pytest
import requests
import trustme
import urllib3.exceptions
import yaml
from _pytest.main import ExitCode
from flask import Flask, jsonify, redirect, request, url_for
from urllib3.exceptions import ProtocolError

from schemathesis.core.shell import ShellType
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi import unregister_string_format
from test.apps._graphql._flask import create_app as create_graphql_app
from test.apps.openapi._flask import create_app as create_openapi_app
from test.utils import HERE, SIMPLE_PATH, flaky


def test_commands_help(cli, snapshot_cli):
    assert cli.main() == snapshot_cli


def test_run_subprocess(testdir):
    # To verify that CLI entry point is installed properly
    result = testdir.run("schemathesis")
    assert result.ret == ExitCode.INTERRUPTED


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
def test_run_as_module(testdir):
    result = testdir.run("python", "-m", "schemathesis.cli")
    assert result.ret == ExitCode.INTERRUPTED


@pytest.mark.parametrize(
    "args",
    [
        (),
        (SIMPLE_PATH,),
        (SIMPLE_PATH, "--url=test"),
        (SIMPLE_PATH, "--url=127.0.0.1:8080"),
        ("http://127.0.0.1", "--request-timeout=-5"),
        ("http://127.0.0.1", "--request-timeout=0"),
        ("http://127.0.0.1", "--auth=123"),
        ("http://127.0.0.1", "--auth=:pass"),
        ("http://127.0.0.1", "--auth=тест:pass"),
        ("http://127.0.0.1", "--auth=user:тест"),
        ("http://127.0.0.1", "--header=123"),
        ("http://127.0.0.1", "--header=:"),
        ("http://127.0.0.1", "--header= :"),
        ("http://127.0.0.1", "--header=тест:test"),
        ("http://127.0.0.1", "--header=test:тест"),
        ("//test",),
        ("http://127.0.0.1", "--max-response-time=0"),
        ("unknown.json",),
        ("unknown.json", "--url=http://127.0.0.1"),
        ("--help",),
        ("http://127.0.0.1", "--generation-codec=foobar"),
        ("http://127.0.0.1", "--report=unknown"),
    ],
)
def test_run_output(cli, args, snapshot_cli):
    assert cli.run(*args) == snapshot_cli


def test_hooks_module_not_found(cli, snapshot_cli):
    # When an unknown hook module is passed to CLI
    assert cli.main("run", "http://127.0.0.1:1", hooks="hook") == snapshot_cli
    assert os.getcwd() in sys.path


def test_hooks_with_inner_import_error(ctx, cli, snapshot_cli):
    # When the hook module itself raises an ImportError
    module = ctx.write_pymodule("import something_else")
    assert cli.main("run", "http://127.0.0.1:1", hooks=module) == snapshot_cli


def test_hooks_invalid(ctx, cli):
    # When hooks are passed to the CLI call
    # And its importing causes an exception
    module = ctx.write_pymodule("1 / 0")

    result = cli.main("run", "http://127.0.0.1:1", hooks=module)

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


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("header", ["Authorization", "authorization"])
def test_auth_and_authorization_header_are_disallowed(cli, schema_url, header, snapshot_cli):
    # When ``--auth`` is passed together with ``--header`` that sets the ``Authorization`` header
    # Then it causes a validation error
    assert cli.run(schema_url, "--auth=test:test", f"--header={header}:token123") == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_schema_not_available(cli, workers, snapshot_cli):
    # When the given schema is unreachable
    # Then the whole Schemathesis run should fail
    # And error message is displayed
    assert cli.run("http://127.0.0.1:1/schema.yaml", f"--workers={workers}") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.snapshot_suffix(platform.python_implementation().lower())
def test_empty_schema_file(testdir, cli, snapshot_cli):
    # When the schema file is empty
    filename = testdir.makefile(".json", schema="")
    # Then a proper error should be reported
    assert cli.run(str(filename), "--url=http://127.0.0.1:1") == snapshot_cli


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
        cli.run_and_assert(schema_url, f"--request-cert={cert_path}")
        # Then both schema & test network calls should use this cert
        assert len(request.call_args_list) == 9
        assert request.call_args_list[0][1]["cert"] == request.call_args_list[1][1]["cert"] == str(cert_path)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_hypothesis_database_with_derandomize(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--generation-database=:memory:", "--generation-deterministic") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations
def test_hypothesis_parameters(cli, schema_url):
    # When Hypothesis options are passed via command line
    cli.run_and_assert(
        schema_url,
        "--generation-deterministic",
        "--max-examples=1000",
        "--suppress-health-check=all",
    )
    # Then they should be correctly converted into arguments accepted by `hypothesis.settings`
    # Parameters are validated in `hypothesis.settings`


@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", [1, 2])
def test_cli_run_only_failure(cli, schema_url, workers, snapshot_cli):
    assert cli.run(schema_url, f"--workers={workers}", "-c not_a_server_error") == snapshot_cli


@pytest.mark.operations("upload_file")
def test_cli_binary_body(cli, schema_url, hypothesis_max_examples):
    result = cli.run_and_assert(
        schema_url,
        "--suppress-health-check=filter_too_much",
        "--mode=positive",
        f"--max-examples={hypothesis_max_examples or 1}",
    )
    assert " HYPOTHESIS OUTPUT " not in result.stdout


@pytest.mark.operations("ignored_auth")
def test_openapi_auth_skips_malformed_security_requirements(cli, ctx, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/ignored_auth": {
                "get": {
                    "security": [
                        None,
                        {"ApiKeyAuth": []},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "name": "X-API-Key",
                    "in": "header",
                }
            }
        },
    )

    result = cli.run(
        str(schema_path),
        f"--url={openapi3_base_url}",
        "--max-examples=1",
        "--checks=not_a_server_error",
        config={"auth": {"openapi": {"ApiKeyAuth": {"api_key": "secret"}}}},
    )

    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.operations
@pytest.mark.parametrize("workers", [1, 2])
def test_cli_run_output_empty(cli, schema_url, workers):
    result = cli.run_and_assert(schema_url, f"--workers={workers}")
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "= Empty test suite =" in lines[-1]


@pytest.mark.openapi_version("3.0")
def test_cli_run_changed_base_url(cli, schema_url, server, snapshot_cli):
    # When the CLI receives custom base URL
    base_url = f"http://127.0.0.1:{server['port']}/api"
    # Then the base URL should be correctly displayed in the CLI output
    assert cli.run(schema_url, "--url", base_url, "-c not_a_server_error") == snapshot_cli


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("/doesnt_exist", "Failed to load schema due to client error (HTTP 404 Not Found)"),
        ("/failure", "Failed to load schema due to server error (HTTP 500 Internal Server Error)"),
    ],
)
@pytest.mark.operations("failure")
@pytest.mark.parametrize("workers", [1, 2])
def test_execute_missing_schema(cli, openapi3_base_url, url, message, workers):
    result = cli.run_and_assert(f"{openapi3_base_url}{url}", f"--workers={workers}", exit_code=ExitCode.TESTS_FAILED)
    assert message in result.stdout


@pytest.mark.operations("success", "slow")
@pytest.mark.parametrize("workers", [1, 2])
def test_connection_timeout(cli, schema_url, workers, snapshot_cli):
    # When connection timeout is specified in the CLI and the request fails because of it
    # Then the whole Schemathesis run should fail
    # And the given operation should be displayed as a failure
    assert cli.run(schema_url, "--request-timeout=0.08", f"--workers={workers}", "--phases=fuzzing") == snapshot_cli


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


@pytest.mark.operations("unsatisfiable")
@pytest.mark.parametrize("workers", [1, 2])
def test_unsatisfiable(cli, schema_url, workers, snapshot_cli):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters' definition - requires to be integer AND string at the
    # same time
    # And more clear error message is displayed instead of Hypothesis one
    assert cli.run(schema_url, "--mode=positive", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("invalid")
def test_invalid_operation(cli, schema_url, snapshot_cli):
    # When the app's schema contains errors
    # For example if its type is "int" but should be "integer"
    # And schema validation is disabled
    assert cli.run(schema_url, "--phases=fuzzing", "--mode=positive") == snapshot_cli


def test_invalid_type_with_ref(cli, ctx, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "in": "header",
                            "name": "h",
                            "schema": {
                                "$ref": "#/components/schemas/S",
                                "type": "invalid",
                            },
                        },
                    ],
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        },
        components={"schemas": {"S": {"maxProperties": 5}}},
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=fuzzing", "--mode=positive") == snapshot_cli
    )


def test_unsatisfiable_with_ref(cli, ctx, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ObjectType",
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        },
        components={
            "schemas": {
                "IntegerType": {"type": "integer", "minimum": 100, "maximum": 1000},
                "StringType": {"type": "string", "pattern": "^[A-Z]{3,10}$"},
                "ObjectType": {
                    "type": "object",
                    "properties": {
                        "nested": {"$ref": "#/components/schemas/NestedSchema"},
                        "another": {
                            "allOf": [
                                {"$ref": "#/components/schemas/IntegerType"},
                                {"$ref": "#/components/schemas/StringType"},
                            ]
                        },
                    },
                    "required": ["nested", "another"],
                },
                "NestedSchema": {"type": "array", "items": {"type": "boolean"}, "minItems": 5},
            }
        },
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=fuzzing", "--mode=positive") == snapshot_cli
    )


def test_unsatisfiable_query_parameter(cli, ctx, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 100, "maximum": 10},
                        }
                    ],
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=fuzzing", "--mode=positive") == snapshot_cli
    )


def test_health_check_message(cli, ctx, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/items/{item_id}/": {
                "patch": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
                        "required": True,
                    }
                }
            }
        },
        components={
            "schemas": {
                "Item": {
                    "type": "string",
                    "format": "date-time",
                    "pattern": "abc",
                }
            }
        },
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=fuzzing") == snapshot_cli


@pytest.mark.operations("teapot")
@pytest.mark.parametrize("workers", [1, 2])
def test_status_code_conformance(cli, schema_url, workers, snapshot_cli):
    # When operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    # Then the whole Schemathesis run should fail
    # And this operation should be marked as failed in the progress line
    assert cli.run(schema_url, "-c", "status_code_conformance", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("headers")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="aiohttp crashes on PyPy")
def test_headers_conformance_valid(cli, schema_url):
    result = cli.run_and_assert(schema_url, "-c", "response_headers_conformance", "-H", "X-Custom-Header: 42")

    lines = result.stdout.split("\n")
    assert "1. Received a response with missing headers: X-Custom-Header" not in lines


@pytest.mark.operations("multiple_failures")
def test_multiple_failures_single_check(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--generation-deterministic",
            "-c not_a_server_error,positive_data_acceptance",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.operations("multiple_failures")
@pytest.mark.openapi_version("3.0")
def test_continue_on_failure(cli, schema_url):
    result = cli.run_and_assert(schema_url, "--continue-on-failure", exit_code=ExitCode.TESTS_FAILED)
    assert "113 generated" in result.stdout


@pytest.mark.operations("multiple_failures")
def test_multiple_failures_different_check(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "-c",
            "status_code_conformance",
            "-c",
            "not_a_server_error",
            "--mode=positive",
            "--generation-deterministic",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_connection_error(cli, schema_url, workers, snapshot_cli):
    # When the given base_url is unreachable
    # Then the whole Schemathesis run should fail
    # And the proper error messages should be displayed for each operation
    assert (
        cli.run(schema_url, "--url=http://127.0.0.1:1/api", f"--workers={workers}", "--mode=positive") == snapshot_cli
    )


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
    assert cli.run(schema_url, "--phases=fuzzing") == snapshot_cli


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
def test_remote_disconnected_error_with_empty_header(mocker, cli, schema_url, snapshot_cli):
    # When a request has an empty header value and the server disconnects
    # Regression test for IndexError when extracting headers from PreparedRequest with empty values
    protocol_error = ProtocolError("Connection aborted.", RemoteDisconnected("Remote end closed connection"))

    def raise_connection_error(self, **kwargs):
        req = requests.Request("GET", "http://127.0.0.1/success", headers={"X-Empty": ""})
        prepared = req.prepare()
        conn_error = requests.ConnectionError(protocol_error)
        conn_error.request = prepared
        conn_error.__context__ = protocol_error
        raise conn_error

    mocker.patch("schemathesis.generation.case.Case.call", raise_connection_error)
    # Then it should not crash with IndexError on empty header value
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--proxy=http://127.0.0.1") == snapshot_cli


@pytest.fixture
def digits_format(ctx):
    module = ctx.write_pymodule(
        """
    import string
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


@pytest.mark.operations("custom_format")
def test_hooks_valid(cli, schema_url, app, digits_format):
    # When a hook is passed to the CLI call
    result = cli.main(
        "run",
        "--suppress-health-check=filter_too_much",
        "--phases=fuzzing",
        "--mode=positive",
        schema_url,
        hooks=digits_format,
    )
    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all registered new string format should produce digits as expected
    assert all(request.query["id"].isdigit() for request in app["incoming_requests"])


@pytest.fixture
def conditional_check(ctx):
    with ctx.check("""
@schemathesis.check
def conditional_check(ctx, response, case):
    # skip this check
    return True
""") as module:
        yield module


def test_conditional_checks(cli, hypothesis_max_examples, schema_url, conditional_check):
    result = cli.main(
        "run",
        "-c",
        "conditional_check",
        schema_url,
        f"--max-examples={hypothesis_max_examples or 1}",
        hooks=conditional_check,
    )

    assert result.exit_code == ExitCode.OK


@pytest.fixture(
    params=[
        'AssertionError("Custom check failed!")',
        "AssertionError",
    ]
)
def new_check(ctx, request, cli):
    exception = request.param
    with ctx.check(
        f"""
@schemathesis.check
def new_check(ctx, response, result):
    raise {exception}
"""
    ) as module:
        yield module
    # To verify that "new_check" is unregistered
    assert "new_check" not in cli.run("--help").stdout


@pytest.mark.operations("success")
def test_register_check(new_check, cli, schema_url, snapshot_cli):
    # When hooks are passed to the CLI call
    # And it contains registering a new check, which always fails for the testing purposes
    # Then CLI run should fail
    # And a message from the new check should be displayed
    assert cli.main("run", "-c", "new_check", "--mode=positive", schema_url, hooks=new_check) == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt(cli, schema_url, base_url, mocker, swagger_20, workers, snapshot_cli):
    # When a Schemathesis run in interrupted by keyboard or via SIGINT
    operation = APIOperation(
        "/success",
        "GET",
        {},
        swagger_20,
        base_url=base_url,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    original = operation.Case().call
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            # For threaded case it emulates SIGINT for the worker thread
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    result = cli.run(schema_url, f"--workers={workers}", "--mode=positive")
    if workers == 1:
        assert result == snapshot_cli
    else:
        assert "skipped" in result.stdout


@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt_threaded(cli, schema_url, mocker, snapshot_cli):
    # When a Schemathesis run is interrupted by the keyboard or via SIGINT
    from schemathesis.engine.phases.unit import DefaultScheduler

    original = DefaultScheduler.next_operation
    counter = 0

    def mocked(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    mocker.patch("schemathesis.engine.phases.unit.DefaultScheduler.next_operation", wraps=mocked)
    assert cli.run(schema_url, "--workers=2", "--generation-deterministic") == snapshot_cli


def test_keyboard_interrupt_during_schema_loading(cli, openapi3_schema_url, mocker, snapshot_cli):
    mocker.patch("schemathesis.core.loaders.make_request", side_effect=KeyboardInterrupt)
    assert cli.run(openapi3_schema_url) == snapshot_cli


async def test_multiple_files_schema(ctx, openapi_2_app, cli, hypothesis_max_examples, openapi2_base_url):
    # When the schema contains references to other files
    uri = pathlib.Path(HERE).as_uri() + "/"
    schema = ctx.openapi.build_schema(
        {
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
        version="2.0",
    )
    schema_path = ctx.makefile(schema)
    openapi_2_app["config"].update({"should_fail": True, "schema_data": schema})
    # And file path is given to the CLI
    result = cli.run(
        str(schema_path),
        f"--url={openapi2_base_url}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--generation-deterministic",
        "-c not_a_server_error",
    )
    # Then Schemathesis should resolve it and run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all relevant requests should contain proper data for resolved references
    payload = await openapi_2_app["incoming_requests"][0].json()
    assert isinstance(payload["name"], str)
    assert isinstance(payload["photoUrls"], list)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"required": []},
        {"required": ["region"]},
    ],
    ids=["no-required", "not-in-required", "in-required"],
)
def test_required_as_boolean(ctx, cli, snapshot_cli, openapi3_base_url, required, kwargs):
    # Happens in the wild, even though it is incorrect
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "region": {
                                            "required": required,
                                            "type": "string",
                                        },
                                    },
                                    "type": "object",
                                    **kwargs,
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "-c not_a_server_error", "--max-examples=5")
        == snapshot_cli
    )


def test_invalid_response_definition(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        # Seen in real-life schema
                                        "properties": "ABC",
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "-c response_schema_conformance", "--max-examples=1")
        == snapshot_cli
    )


def test_no_useless_traceback(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--mode=positive") == snapshot_cli


def test_invalid_yaml(testdir, cli, simple_openapi, snapshot_cli, openapi3_base_url):
    schema = yaml.dump(simple_openapi)
    schema += "\x00"
    schema_file = testdir.makefile(".yaml", schema=schema)
    assert cli.run(str(schema_file), f"--url={openapi3_base_url}") == snapshot_cli


@pytest.fixture
def with_error(ctx):
    with ctx.check(
        """
@schemathesis.check
def with_error(ctx, response, case):
    1 / 0
"""
    ) as module:
        yield module


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(
    sys.version_info < (3, 11) or sys.version_info >= (3, 13) or platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
def test_useful_traceback(cli, schema_url, snapshot_cli, with_error):
    assert cli.main("run", schema_url, "-c", "with_error", hooks=with_error) == snapshot_cli


@pytest.mark.parametrize("media_type", ["multipart/form-data", "multipart/mixed", "multipart/*"])
def test_multipart_upload(ctx, tmp_path, hypothesis_max_examples, openapi3_base_url, cli, media_type):
    cassette_path = tmp_path / "output.yaml"
    # When requestBody has a binary field or an array of binary items
    responses = {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    schema_path = ctx.openapi.write_schema(
        {
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
                                    "additionalProperties": False,
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
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": responses,
                }
            },
        }
    )
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={openapi3_base_url}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--generation-deterministic",
        f"--report-vcr-path={cassette_path}",
        "-c not_a_server_error",
        "--mode=positive",
    )
    # Then it should be correctly sent to the server
    assert "= ERRORS =" not in result.stdout

    with cassette_path.open(encoding="utf-8") as fd:
        raw = fd.read()
        cassette = yaml.safe_load(raw)

    def decode(idx):
        request = cassette["http_interactions"][idx]["request"]
        if "body" not in request:
            return None
        return request["body"]["string"].encode()

    first_decoded = decode(0)
    if first_decoded:
        assert b'Content-Disposition: form-data; name="file"; filename="file"\r\n' in first_decoded
    last_decoded = decode(-1)
    if last_decoded:
        assert b'Content-Disposition: form-data; name="files"; filename="files"\r\n' in last_decoded
    # NOTE, that the actual API operation is not checked in this test


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize(
    "field_name,field_schema,content_type",
    [
        ("image", {"type": "string", "format": "binary"}, "image/png"),
        ("metadata", {"type": "string"}, "application/json"),
        ("files", {"type": "array", "items": {"type": "string", "format": "binary"}}, "image/jpeg"),
    ],
    ids=["binary", "string", "array"],
)
def test_multipart_encoding_content_type(ctx, cli, app_runner, snapshot_cli, field_name, field_schema, content_type):
    app = Flask(__name__)
    schema_def = {
        "type": "object",
        "properties": {field_name: field_schema},
        "required": [field_name],
    }
    spec = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": schema_def,
                                "encoding": {field_name: {"contentType": content_type}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

    @app.route("/upload", methods=["POST"])
    def upload():
        # Check if field exists (could be in files or form)
        if field_name in request.files:
            file_obj = request.files[field_name]
            if file_obj.content_type != content_type:
                return jsonify({"error": f"Expected {content_type}, got: {file_obj.content_type}"}), 500
        # Accept any request as long as content type is correct when field is present
        return jsonify({"status": "ok"}), 200

    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--phases=fuzzing", "--max-examples=5", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_no_schema_in_media_type(ctx, cli, base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/property": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": {}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={base_url}", "--max-examples=1", "--checks=not_a_server_error")
        == snapshot_cli
    )


def test_nested_binary_in_yaml(ctx, openapi3_base_url, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--max-examples=10", "-c not_a_server_error")
        == snapshot_cli
    )


@pytest.mark.operations("form")
def test_urlencoded_form(cli, schema_url):
    # When the API operation accepts application/x-www-form-urlencoded
    # Then Schemathesis should generate appropriate payload
    cli.run_and_assert(schema_url, "--mode=positive")


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.operations("success")
def test_targeted(mocker, cli, schema_url, workers):
    target = mocker.spy(hypothesis, "target")
    cli.run_and_assert(schema_url, f"--workers={workers}", "--generation-maximize=response_time")

    target.assert_called_with(mocker.ANY, label="response_time")


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (
            ("--exclude-deprecated",),
            "Selected: 1/2",
        ),
        (
            (),
            "Selected: 2/2",
        ),
    ],
)
def test_exclude_deprecated(ctx, cli, openapi3_base_url, options, expected):
    # When there are some deprecated API operations
    definition = {
        "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    }
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": definition,
                "post": {
                    "deprecated": True,
                    **definition,
                },
            }
        }
    )
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={openapi3_base_url}",
        "--max-examples=1",
        "--checks=not_a_server_error",
        *options,
    )
    # Then only not deprecated API operations should be selected
    assert expected in result.stdout


@pytest.mark.openapi_version("3.0")
def test_duplicated_filters(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--include-path=success", "--include-path=success") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_invalid_filter(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--include-by=fooo") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_filter_case_sensitivity(cli, schema_url, snapshot_cli):
    # Method filter should be case insensitive
    assert cli.run(schema_url, "--include-method=get", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.parametrize("value", ["--include-by=/x-property == 42", "--exclude-by=/x-property != 42"])
@pytest.mark.operations("upload_file", "custom_format")
@pytest.mark.openapi_version("3.0")
def test_filter_by(cli, schema_url, snapshot_cli, value):
    assert cli.run(schema_url, "--mode=positive", "--max-examples=1", value) == snapshot_cli


@pytest.mark.operations("success")
def test_colon_in_headers(cli, schema_url, app):
    header = "X-FOO"
    value = "bar:spam"
    cli.run_and_assert(schema_url, f"--header={header}:{value}")

    assert app["incoming_requests"][0].headers[header] == value


@pytest.mark.openapi_version("3.0")
def test_yaml_parsing_of_floats(cli, testdir, base_url):
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
    result = cli.run_and_assert(
        str(schema_file),
        f"--url={base_url}",
        "--phases=fuzzing",
        "--checks=not_a_server_error",
        "--mode=positive",
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert "Invalid `pattern` value: expected a string" in result.stdout


@pytest.mark.operations("slow")
@pytest.mark.parametrize("workers", [1, 2])
def test_max_response_time_invalid(cli, schema_url, workers, snapshot_cli):
    # When maximum response time check is specified in the CLI and the request takes more time
    # Then the whole Schemathesis run should fail
    # And the given operation should be displayed as a failure
    # And the proper error message should be displayed
    assert cli.run(schema_url, "--max-response-time=0.05", f"--workers={workers}") == snapshot_cli


@pytest.mark.operations("slow")
def test_max_response_time_valid(cli, schema_url):
    # When maximum response time check is specified in the CLI and the request takes less time
    # Then no errors should occur
    cli.run_and_assert(schema_url, "--max-response-time=200")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "success")
@pytest.mark.snapshot(remove_last_line=True)
def test_exit_first(cli, schema_url, snapshot_cli):
    # When the `--max-failures=1` CLI option is passed
    # And a failure occurs
    assert cli.run(schema_url, "--max-failures=1", "--phases=fuzzing", "--checks=not_a_server_error") == snapshot_cli


def test_long_operation_output(ctx, cli, openapi3_base_url, snapshot_cli):
    # See GH-990
    # When there is a narrow screen
    # And the API schema contains an operation with a long name
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    # Then this operation name should be truncated
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "-c not_a_server_error") == snapshot_cli


def test_reserved_characters_in_operation_name(ctx, cli, snapshot_cli, openapi3_base_url):
    # See GH-992
    # When an API operation name contains `:`
    schema_path = ctx.openapi.write_schema(
        {
            "/foo:bar": {
                "get": {
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    # Then this operation name should be displayed with the leading `/`
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}") == snapshot_cli


def test_unsupported_regex(ctx, cli, snapshot_cli, openapi3_base_url):
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
    schema_path = ctx.openapi.write_schema(
        {
            # Can't generate anything
            "/foo": make_definition(min_items=1),
            # Can generate an empty array
            "/bar": make_definition(min_items=0),
        }
    )
    # Then if it is possible it should generate at least something
    # And if it is not then there should be an error with a descriptive error message
    assert (
        cli.run(
            str(schema_path),
            "--max-examples=1",
            f"--url={openapi3_base_url}",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("extra", ["--auth='test:wrong'", "-H Authorization: Basic J3Rlc3Q6d3Jvbmcn"])
@pytest.mark.operations("basic")
def test_auth_override_on_protected_operation(cli, schema_url, extra, snapshot_cli):
    # See GH-792
    # When the tested API operation has basic auth
    # And the auth is overridden (directly or via headers)
    # And there is an error during testing
    # Then the code sample representation in the output should have the overridden value
    assert cli.run(schema_url, "--output-sanitize=false", "--phases=fuzzing", extra) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("flaky")
def test_explicit_headers_in_output_on_errors(cli, schema_url, snapshot_cli):
    # When there is a non-fatal error during testing (e.g. flakiness)
    # And custom headers were passed explicitly
    auth = "Basic J3Rlc3Q6d3Jvbmcn"
    # Then the code sample should have the overridden value
    assert (
        cli.run(schema_url, "--output-sanitize=false", f"-H Authorization: {auth}", "--mode=positive") == snapshot_cli
    )


@pytest.mark.operations("cp866")
def test_response_payload_encoding(cli, schema_url, snapshot_cli):
    # See GH-1073
    # When the "failed" response has non UTF-8 encoding
    # Then it should be displayed according its actual encoding
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.operations("conformance")
@pytest.mark.snapshot(replace_test_cases=False)
def test_response_schema_conformance_deduplication(cli, schema_url, snapshot_cli):
    # See GH-907
    # When the "response_schema_conformance" check is present
    # And the app return different error messages caused by the same validator
    # Then the errors should be deduplicated
    assert cli.run(schema_url, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("malformed_json")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_malformed_json_deduplication(cli, schema_url, snapshot_cli):
    # See GH-1518
    # When responses are not JSON as expected and their content differ each time
    # Then the errors should be deduplicated
    assert cli.run(schema_url, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.parametrize("kind", ["env_var", "arg"])
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_no_color(monkeypatch, cli, schema_url, kind):
    args = (schema_url,)
    if kind == "env_var":
        monkeypatch.setenv("NO_COLOR", "1")
    if kind == "arg":
        args += ("--no-color",)
    result = cli.run_and_assert(*args, color=True)

    assert "36m" not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.skipif(platform.system() == "Windows", reason="ANSI colors are not properly supported in Windows tests")
def test_force_color(cli, schema_url):
    # Using `--force-color` adds ANSI escape codes forcefully
    result = cli.run_and_assert(schema_url, "--force-color", color=False)

    assert "[1m" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--checks", "all"),
    ],
)
@pytest.mark.parametrize("graphql_path", ["/graphql", "/foo"])
def test_graphql_url(cli, graphql_url, graphql_path, args, snapshot_cli):
    # When the target API is GraphQL
    assert cli.run(graphql_url, "--max-examples=5", *args) == snapshot_cli


@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
def test_missing_content_and_schema(ctx, cli, location, snapshot_cli, openapi3_base_url):
    # When an Open API 3 parameter is missing `schema` & `content`
    schema_path = ctx.openapi.write_schema(
        {"/foo": {"get": {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}}}
    )
    # Then CLI should show that this API operation errored
    # And show the proper message under its "ERRORS" section
    assert cli.run(str(schema_path), "--max-examples=1", f"--url={openapi3_base_url}") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure")
def test_explicit_query_token_sanitization(ctx, cli, snapshot_cli, base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/failure": {
                "get": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        security=[{"api_key": []}],
        components={
            "securitySchemes": {
                "api_key": {
                    "type": "apiKey",
                    "name": "token",
                    "in": "query",
                },
            }
        },
    )
    token = "secret"
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={base_url}",
        "-c not_a_server_error",
        config={"parameters": {"token": token}},
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert result == snapshot_cli
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_skip_not_negated_tests(cli, schema_url, snapshot_cli):
    # See GH-1463
    # When an endpoint has no parameters to negate
    # Then it should be skipped
    assert cli.run(schema_url, "--mode", "negative") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_dont_skip_when_generation_is_possible(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--mode", "all") == snapshot_cli


@pytest.mark.operations("failure")
def test_explicit_example_failure_output(ctx, cli, openapi3_base_url, snapshot_cli):
    # When an explicit example fails
    schema_path = ctx.openapi.write_schema(
        {
            "/failure": {
                "get": {
                    "parameters": [{"in": "query", "name": "key", "example": "foo", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


@pytest.mark.operations("failure")
def test_curl_with_non_printable_characters(ctx, cli, openapi3_base_url, snapshot_cli, monkeypatch):
    monkeypatch.setattr("schemathesis.core.shell._DETECTED_SHELL", ShellType.BASH)

    schema_path = ctx.openapi.write_schema(
        {
            "/failure": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {
                                "schema": {"type": "string"},
                                "example": "line1\nline2\ttab\x1fcontrol",
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


@pytest.mark.operations("failure")
@pytest.mark.skipif(platform.system() == "Windows", reason="Requires more complex setup")
def test_curl_with_non_printable_characters_unknown_shell(ctx, cli, openapi3_base_url, snapshot_cli, monkeypatch):
    monkeypatch.setattr("schemathesis.core.shell._DETECTED_SHELL", ShellType.UNKNOWN)

    schema_path = ctx.openapi.write_schema(
        {
            "/failure": {
                "post": {
                    "parameters": [
                        {
                            "in": "header",
                            "name": "X-Custom",
                            "schema": {"type": "string"},
                            "example": "test\x00value",
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {
                                "schema": {"type": "string"},
                                "example": "data\x1f",
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


@pytest.mark.operations("success")
def test_skipped_on_no_explicit_examples(cli, openapi3_schema_url, snapshot_cli):
    # See GH-1323
    # When there are no explicit examples
    # Then tests should be marked as skipped
    assert cli.run(openapi3_schema_url, "--phases=examples") == snapshot_cli


@pytest.fixture
def data_generation_check(ctx):
    with ctx.check(
        """
@schemathesis.check
def data_generation_check(ctx, response, case):
    if case.meta.generation.mode:
        note("MODE: {}".format(case.meta.generation.mode.value))
"""
    ) as module:
        yield module


@flaky(max_runs=5, min_passes=1)
@pytest.mark.operations("payload")
def test_multiple_generation_modes(cli, openapi3_schema_url, data_generation_check):
    # When multiple data generation methods are supplied in CLI
    result = cli.main(
        "run",
        "-c",
        "data_generation_check",
        "-c",
        "not_a_server_error",
        openapi3_schema_url,
        "--max-examples=25",
        "--suppress-health-check=all",
        "--mode",
        "all",
        hooks=data_generation_check,
    )
    # Then there should be cases generated from different methods
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "MODE: positive" in result.stdout
    assert "MODE: negative" in result.stdout


@pytest.mark.parametrize(
    ("schema_path", "app_factory"),
    (
        [
            (
                "schema.yaml",
                lambda: create_openapi_app(operations=("success",)),
            ),
            (
                "graphql",
                create_graphql_app,
            ),
        ]
    ),
)
def test_wait_for_schema(cli, schema_path, app_factory, app_runner):
    # When Schemathesis is asked to wait for API schema to become available
    app = app_factory()
    original_run = app.run

    def run_with_delay(*args, **kwargs):
        time.sleep(0.1)
        return original_run(*args, **kwargs)

    app.run = run_with_delay
    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/{schema_path}"
    cli.run_and_assert(schema_url, "--wait-for-schema=1", "--max-examples=1")


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
def test_wait_for_schema_not_enough(cli, snapshot_cli, app_runner):
    app = create_openapi_app(operations=("success",))
    original_run = app.run

    def run_with_delay(*args, **kwargs):
        time.sleep(2)
        return original_run(*args, **kwargs)

    app.run = run_with_delay
    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/schema.yaml"

    assert cli.run(schema_url, "--wait-for-schema=1", "--max-examples=1") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_rate_limit(cli, schema_url):
    assert cli.run(schema_url, "--rate-limit=1/s").exit_code == ExitCode.OK


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure")
def test_invalid_tls_verify(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url.replace("http", "https"), "--tls-verify=falst") == snapshot_cli


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_invalid_schema_with_disabled_validation(
    ctx, cli, openapi_3_schema_with_invalid_security, version, snapshot_cli, openapi3_base_url
):
    # When there is an error in the schema
    openapi_3_schema_with_invalid_security["openapi"] = version
    schema_path = ctx.makefile(openapi_3_schema_with_invalid_security)
    # And the validation is disabled (default)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}") == snapshot_cli


def test_unresolvable_reference(ctx, cli, open_api_3_schema_with_recoverable_errors, snapshot_cli, openapi3_base_url):
    # When there is an error in the schema
    del open_api_3_schema_with_recoverable_errors["paths"]["/bar"]["get"]
    schema_path = ctx.makefile(open_api_3_schema_with_recoverable_errors)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}") == snapshot_cli


@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.operations("failure")
def test_output_sanitization(cli, openapi2_schema_url, hypothesis_max_examples, value):
    auth = "secret-auth"
    result = cli.run_and_assert(
        openapi2_schema_url,
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
        exit_code=ExitCode.TESTS_FAILED,
    )

    if value == "false":
        expected = f"curl -X GET -H 'Authorization: {auth}'"
    else:
        expected = "curl -X GET -H 'Authorization: [Filtered]'"
    assert expected in result.stdout


@pytest.mark.parametrize("override", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.operations("failure")
def test_output_sanitization_via_config(cli, openapi2_schema_url, hypothesis_max_examples, enabled, override):
    auth = "secret-auth"
    args = ()
    if override:
        # Should differ from the config file
        if enabled:
            args = ("--output-sanitize=false",)
        else:
            args = ("--output-sanitize=true",)
    result = cli.run_and_assert(
        openapi2_schema_url,
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        *args,
        config={"output": {"sanitization": {"enabled": enabled}}},
        exit_code=ExitCode.TESTS_FAILED,
    )

    if override:
        if enabled:
            # Config enables sanitization, CLI disables it
            expected = f"curl -X GET -H 'Authorization: {auth}'"
        else:
            # Config disables sanitization, CLI enables it
            expected = "curl -X GET -H 'Authorization: [Filtered]'"
    else:
        if enabled:
            # Config enables sanitization
            expected = "curl -X GET -H 'Authorization: [Filtered]'"
        else:
            # Config disables sanitization
            expected = f"curl -X GET -H 'Authorization: {auth}'"
    assert expected in result.stdout, result.stdout


@pytest.mark.operations("success")
@flaky(max_runs=5, min_passes=1)
def test_multiple_failures_in_single_check(ctx, mocker, response_factory, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/json": {"schema": {"type": "integer"}}},
                        }
                    }
                },
            },
        }
    )
    response = response_factory.requests(content_type=None, status_code=200)
    mocker.patch("requests.Session.request", return_value=response)
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--checks=all", "--mode=positive") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_binary_payload(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--exclude-checks=positive_data_acceptance",
        )
        == snapshot_cli
    )


@flaky(max_runs=5, min_passes=1)
def test_long_payload(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/long": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/json": {"schema": {"type": "array"}}},
                        }
                    }
                },
            },
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--exclude-checks=positive_data_acceptance",
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Snapshot is inaccurate on Windows")
@flaky(max_runs=5, min_passes=1)
def test_multiple_errors(ctx, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    assert cli.run(str(schema_path), "--url=http://127.0.0.1:1") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_group_errors(ctx, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
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
    )
    assert cli.run(str(schema_path), "--url=http://127.0.0.1:1") == snapshot_cli


@flaky(max_runs=5, min_passes=1)
def test_complex_urlencoded_example(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "invalid": {"schema": {"example": 1}},
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "example": [
                                        {
                                            "tag": "0",
                                            "timestamp": "2016-04-07T19:39:18Z",
                                            "url": "http://127.0.0.1:8001",
                                        },
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
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=examples", "--checks=not_a_server_error")
        == snapshot_cli
    )


@pytest.fixture
def custom_strings(ctx):
    with ctx.check(
        """
@schemathesis.check
def custom_strings(ctx, response, case):
    if not isinstance(case.body, str):
        return
    try:
        case.body.encode("ascii")
    except Exception as exc:
        raise AssertionError(str(exc))
    assert "\\x00" not in case.body
"""
    ) as module:
        yield module


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("plain_text_body")
def test_custom_strings(cli, hypothesis_max_examples, schema_url, custom_strings):
    result = cli.main(
        "run",
        "-c",
        "custom_strings",
        "--generation-allow-x00=false",
        "--generation-codec=ascii",
        schema_url,
        f"--max-examples={hypothesis_max_examples or 100}",
        hooks=custom_strings,
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.fixture
def verify_overrides(ctx):
    with ctx.check(
        """
@schemathesis.check
def verify_overrides(ctx, response, case):
    if "key" in case.operation.path_parameters:
        assert case.path_parameters["key"] == "foo"
        assert "id" not in (case.query or {}), "`id` is present"
    if "id" in case.operation.query:
        assert case.query["id"] == "bar"
        assert "key" not in (case.path_parameters or {}), "`key` is present"
"""
    ) as module:
        yield module


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("path_variable", "custom_format")
def test_parameter_overrides(cli, schema_url, verify_overrides):
    result = cli.main(
        "run",
        "-c",
        "verify_overrides",
        "--phases=fuzzing",
        schema_url,
        hooks=verify_overrides,
        config={"parameters": {"key": "foo", "id": "bar"}},
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.parametrize(
    ["args", "config"],
    (
        (
            ("--max-redirects=5",),
            {},
        ),
        (
            (),
            {"max-redirects": 5},
        ),
    ),
)
def test_max_redirects(cli, app_runner, snapshot_cli, args, config):
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Redirect Test", "version": "1.0.0"},
        "paths": {
            "/redirect": {
                "get": {
                    "responses": {
                        "302": {"description": "Redirect"},
                        "200": {
                            "description": "Success",
                        },
                    },
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/redirect", methods=["GET"])
    def redirect_endpoint():
        # Infinite loop
        return redirect(url_for("redirect_endpoint"), code=302)

    port = app_runner.run_flask_app(app)

    assert (
        cli.main(
            "run",
            "--phases=fuzzing",
            "--max-examples=1",
            f"http://127.0.0.1:{port}/openapi.json",
            *args,
            config=config,
        )
        == snapshot_cli
    )


@pytest.fixture
def no_null_bytes(ctx):
    with ctx.check(
        r"""
@schemathesis.check
def no_null_bytes(ctx, response, case):
    assert "\x00" not in case.headers.get("X-KEY", {})
"""
    ) as module:
        yield module


def test_null_byte_in_header_probe(ctx, cli, snapshot_cli, openapi3_base_url, no_null_bytes):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [{"name": "X-KEY", "in": "header", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.main(
            "run",
            str(schema_path),
            "-c",
            "no_null_bytes",
            f"--url={openapi3_base_url}",
            "--max-examples=1",
            hooks=no_null_bytes,
        )
        == snapshot_cli
    )


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="Error message is different")
def test_malformed_schema(testdir, cli, snapshot_cli, openapi3_base_url):
    schema_path = testdir.makefile(
        ".json",
        schema="""
{
   "swagger": "2.0",
}
    """,
    )
    assert cli.main("run", str(schema_path), f"--url={openapi3_base_url}", "--max-examples=1") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_recursive_reference_error_message(ctx, cli, schema_with_recursive_references, openapi3_base_url, snapshot_cli):
    schema_path = ctx.makefile(schema_with_recursive_references)
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--mode=positive") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="May behave differently on Windows")
def test_empty_reference_does_not_cause_infinite_recursion(ctx, cli, openapi3_base_url, snapshot_cli):
    # Empty $ref should be gracefully skipped during bundling, not cause RecursionError
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "put": {
                    "parameters": [{"in": "body", "name": "body", "schema": {"$ref": "#/definitions/Connection"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        definitions={
            "Connection": {
                "allOf": [{"$ref": "#/definitions/Resource"}],
                "properties": {
                    "key": {
                        "properties": {
                            "key": {
                                "$ref": ""  # Empty reference - should be skipped
                            }
                        }
                    }
                },
            },
            "Resource": {},
        },
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=examples") == snapshot_cli


def test_nullable_reference(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q",
                            "schema": {
                                "$ref": "#/components/schemas/MessageStatus",
                                "nullable": True,
                            },
                        }
                    ],
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        },
        components={"schemas": {"MessageStatus": {}}},
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--mode=positive", "--phases=fuzzing") == snapshot_cli
    )


def test_reference_in_examples(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {"$ref": "#components/parameters/metadata"},
                        {"$ref": "#components/parameters/applicationId"},
                    ]
                }
            }
        },
        components={
            "parameters": {
                "applicationId": {
                    "example": 0,
                    "in": "header",
                    "name": "xd",
                    "schema": {},
                },
                "metadata": {
                    "content": {"text/plain": {"schema": {"$ref": "t"}}},
                    "in": "header",
                    "name": "xa",
                },
            }
        },
    )
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--phases=examples") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("payload")
@pytest.mark.skipif(sys.version_info >= (3, 13), reason="Traceback is different")
def test_unknown_schema_error(ctx, schema_url, cli, snapshot_cli):
    module = ctx.write_pymodule(
        r"""
import schemathesis

@schemathesis.metric
def buggy(ctx):
    raise AssertionError("Something bad happen")
"""
    )
    assert (
        cli.main(
            "run",
            schema_url,
            "--generation-maximize=buggy",
            "-c not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_custom_cli_option(ctx, cli, schema_url, snapshot_cli):
    module = ctx.write_pymodule(
        r"""
from schemathesis import cli, engine


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

    def handle_event(self, ctx, event) -> None:
        self.counter += 1
        if isinstance(event, engine.events.EngineStarted):
            ctx.add_initialization_line("Counter initialized!")
            ctx.add_initialization_line(gen())
        elif isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"Counter: {self.counter}")
            ctx.add_summary_line(gen())
"""
    )
    assert (
        cli.main(
            "run",
            schema_url,
            "--custom-counter=42",
            "--max-examples=1",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ["ordering_mode", "expected"],
    [
        pytest.param("none", ["GET /users/{id}", "DELETE /users/{id}", "POST /users", "GET /users"], id="none"),
        # auto mode: Layer 0 (sorted): GET /users, POST /users -> Layer 1: GET /users/{id} -> Layer 2: DELETE /users/{id}
        pytest.param("auto", ["GET /users", "POST /users", "GET /users/{id}", "DELETE /users/{id}"], id="auto"),
    ],
)
def test_operation_ordering(ctx, cli, app_runner, ordering_mode, expected):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
        {
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    },
                },
                "delete": {
                    "operationId": "deleteUser",
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"id": "$response.body#/id"},
                                },
                                "DeleteUser": {
                                    "operationId": "deleteUser",
                                    "parameters": {"id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                },
                "get": {"operationId": "listUsers", "responses": {"200": {"description": "OK"}}},
            },
        },
        version="3.0.0",
    )

    @app.route("/openapi.json")
    def openapi_spec():
        return app.response_class(response=json.dumps(spec, sort_keys=False), status=200, mimetype="application/json")

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        return jsonify({"id": user_id})

    @app.route("/users/<int:user_id>", methods=["DELETE"])
    def delete_user(user_id):
        return "", 204

    @app.route("/users", methods=["GET", "POST"])
    def users():
        return jsonify({"success": True}), 200 if request.method == "GET" else 201

    port = app_runner.run_flask_app(app)

    module = ctx.write_pymodule(
        """
from schemathesis import cli, engine

@cli.handler()
class OperationOrderTracker(cli.EventHandler):
    def __init__(self, *args, **params):
        self.operations = []

    def handle_event(self, ctx, event):
        if isinstance(event, engine.events.ScenarioFinished):
            self.operations.append(event.label)
        elif isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"OPERATION_ORDER: {','.join(self.operations)}")
        """
    )

    result = cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        "--max-examples=1",
        "--workers=1",
        "--continue-on-failure",
        "--phases=fuzzing",
        hooks=module,
        config={"phases": {"fuzzing": {"operation-ordering": ordering_mode}}},
    )

    for line in result.stdout.split("\n"):
        if line.startswith("OPERATION_ORDER:"):
            actual_order = line.split(":", 1)[1].strip().split(",")
            break
    else:
        raise AssertionError("OPERATION_ORDER marker not found in output")

    assert actual_order == expected, (
        f"Operation order mismatch for mode={ordering_mode}\nExpected: {expected}\nActual:   {actual_order}"
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.skipif(platform.system() == "Windows", reason="Requires a more complex test setup")
def test_rediscover_the_same_failure_in_different_phases_and_store_junit(ctx, cli, schema_url, tmp_path, snapshot_cli):
    # See GH-2814
    report_dir = tmp_path / "reports"
    with ctx.check(
        r"""
@schemathesis.check
def always_fails(ctx, response, case):
    if case.operation.label == "GET /users/{user_id}":
        raise AssertionError("Failed!")
"""
    ) as module:
        assert (
            cli.main(
                "run",
                schema_url,
                "-c",
                "always_fails",
                "--max-examples=1",
                f"--report-dir={report_dir}",
                f"--report-junit-path={report_dir}/junit.xml",
                "--report=junit",
                "--mode=positive",
                hooks=module,
            )
            == snapshot_cli
        )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_app_crash(subprocess_runner, cli, snapshot_cli):
    app = """
import os
from flask import Flask, jsonify
import ctypes

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Crash Test", "version": "1.0.0"},
    "paths": {"/crash": {"get": {"responses": {"200": {"description": "Won't return"}}}}},
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.get("/crash")
def crash():
    ctypes.string_at(0)  # Segfault

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--tls-verify=false") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_partial_response(subprocess_runner, cli, snapshot_cli):
    app = """
import os
import sys
from flask import Flask, jsonify, Response
import time

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Partial Response Test", "version": "1.0.0"},
    "paths": {"/crash": {"get": {"responses": {"200": {"description": "Won't return"}}}}},
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.get("/crash")
def crash():
    def generate():
        yield '{"partial":'
        sys.stdout.flush()
        # Force connection reset while client expects more data
        os._exit(1)

    return Response(generate(), mimetype='application/json')

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--tls-verify=false") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.snapshot(replace_phase_statistic=True)
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_stateful_crash(subprocess_runner, cli, snapshot_cli):
    app = """
import os
from flask import Flask, jsonify, request
import ctypes

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Stateful Crash Test", "version": "1.0.0"},
    "paths": {
        "/users": {
            "post": {
                "summary": "Create user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"]
                            }
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "User created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}},
                                    "required": ["id"]
                                }
                            }
                        },
                        "links": {
                            "GetUserById": {
                                "operationId": "getUserById",
                                "parameters": {"id": "$response.body#/id"}
                            }
                        }
                    }
                }
            }
        },
        "/users/{id}": {
            "get": {
                "operationId": "getUserById",
                "summary": "Get user by ID",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "User details",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.route("/users", methods=["POST"])
def create_user():
    return jsonify({"id": 123}), 201

@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    # Crash during stateful testing when following the link
    ctypes.string_at(0)  # Segfault

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "--tls-verify=false",
            "-c not_a_server_error",
            "--max-examples=1",
            "--mode=positive",
        )
        == snapshot_cli
    )
