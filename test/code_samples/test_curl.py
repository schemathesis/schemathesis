import pytest
from _pytest.main import ExitCode
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis import Case
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.shell import ShellType
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.generation.modes import GenerationMode
from test.apps.openapi._fastapi import create_app
from test.apps.openapi._fastapi.app import app

schema = schemathesis.openapi.from_dict(app.openapi())
schema.config.generation.update(modes=[GenerationMode.POSITIVE])


@pytest.fixture
def loose_schema(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test/{key}": {
                "post": {
                    "parameters": [{"name": "key", "in": "path"}],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.update(base_url="http://127.0.0.1:1")
    return schema


@pytest.mark.parametrize("headers", [None, {"X-Key": "42"}, {"X-Key": ""}])
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_as_curl_command(case: Case, headers, curl):
    case.operation.schema.config.output.sanitization.update(enabled=False)
    command = case.as_curl_command(headers)
    expected_headers = ""
    if headers:
        for name, value in headers.items():
            if not value:
                expected_headers += f" -H '{name};'"
            else:
                expected_headers += f" -H '{name}: {value}'"
    assert command == f"curl -X GET{expected_headers} http://localhost/users"
    curl.assert_valid(command)


def test_non_utf_8_body(curl):
    case = schema["/users"]["GET"].Case(body=b"42\xff", media_type="application/octet-stream")
    command = case.as_curl_command()
    assert command == "curl -X GET -H 'Content-Type: application/octet-stream' -d '42�' http://localhost/users"
    curl.assert_valid(command)


def test_json_payload(curl):
    new_app = create_app(operations=["create_user"])
    schema = schemathesis.openapi.from_dict(new_app.openapi())
    case = schema["/users/"]["POST"].Case(body={"foo": 42}, media_type="application/json")
    command = case.as_curl_command()
    assert command == "curl -X POST -H 'Content-Type: application/json' -d '{\"foo\": 42}' http://localhost/users/"
    curl.assert_valid(command)


def test_explicit_headers(curl):
    # When the generated case contains a header from the list of headers that are ignored by default
    name = "Accept"
    value = "application/json"
    case = schema["/users"]["GET"].Case(headers={name: value})
    command = case.as_curl_command()
    assert command == f"curl -X GET -H '{name}: {value}' http://localhost/users"
    curl.assert_valid(command)


@pytest.mark.operations("failure")
@pytest.mark.openapi_version("3.0")
def test_cli_output(cli, base_url, schema_url, curl):
    result = cli.run_and_assert(schema_url, exit_code=ExitCode.TESTS_FAILED)
    lines = result.stdout.splitlines()
    assert "Reproduce with:" in lines
    line = f"    curl -X GET {base_url}/failure"
    assert line in lines
    command = line.strip()
    curl.assert_valid(command)


@pytest.mark.operations("failure")
@pytest.mark.openapi_version("3.0")
def test_cli_output_includes_insecure(cli, base_url, schema_url, curl):
    result = cli.run_and_assert(schema_url, "--tls-verify=false", exit_code=ExitCode.TESTS_FAILED)
    lines = result.stdout.splitlines()
    line = f"    curl -X GET --insecure {base_url}/failure"
    assert line in lines
    command = line.strip()
    curl.assert_valid(command)


@pytest.mark.operations("failure")
def test_pytest_subtests_output(testdir, openapi3_base_url, app_schema):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_(case):
    case.call_and_validate()
""",
        schema=app_schema,
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines([".+ Reproduce with:", f".+ curl -X GET {openapi3_base_url}/failure"])


@pytest.mark.hypothesis_nested
def test_curl_command_validity(curl, loose_schema):
    # When the input schema is too loose

    @given(case=loose_schema["/test/{key}"]["POST"].as_strategy())
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
    def test(case):
        command = case.as_curl_command()
        # Then generated command should always be syntactically valid
        curl.assert_valid(command)

    test()


@pytest.mark.parametrize(
    ("shell_type", "case_kwargs", "expected_command"),
    [
        # Bash/zsh: ANSI-C quoting for headers
        pytest.param(
            ShellType.BASH,
            {"headers": {"X-Test": "value\x1f"}},
            "curl -X GET -H $'X-Test: value\\x1f' http://localhost/users",
            id="bash-header-control-char",
        ),
        # Fish: hex escaping in quotes for headers
        pytest.param(
            ShellType.FISH,
            {"headers": {"X-Test": "value\x1f"}},
            "curl -X GET -H 'X-Test: value\\x1f' http://localhost/users",
            id="fish-header-control-char",
        ),
        # Bash: ANSI-C quoting for body with null byte
        pytest.param(
            ShellType.BASH,
            {"body": "test\x00data", "media_type": "text/plain"},
            "curl -X GET -H 'Content-Type: text/plain' -d $'test\\x00data' http://localhost/users",
            id="bash-body-null-byte",
        ),
        # Bash: ANSI-C quoting for body with tab and newline
        pytest.param(
            ShellType.BASH,
            {"body": "line1\nline2\ttab", "media_type": "text/plain"},
            "curl -X GET -H 'Content-Type: text/plain' -d $'line1\\nline2\\ttab' http://localhost/users",
            id="bash-body-tab-newline",
        ),
        # ZSH: same as bash (ANSI-C quoting)
        pytest.param(
            ShellType.ZSH,
            {"headers": {"X-Test": "value\x1f"}},
            "curl -X GET -H $'X-Test: value\\x1f' http://localhost/users",
            id="zsh-header-control-char",
        ),
        # No monkeypatch: printable strings use standard quoting
        pytest.param(
            None,
            {"headers": {"X-Test": "normal value"}},
            "curl -X GET -H 'X-Test: normal value' http://localhost/users",
            id="printable-header",
        ),
    ],
)
def test_shell_aware_escaping(curl, monkeypatch, shell_type, case_kwargs, expected_command):
    if shell_type is not None:
        monkeypatch.setattr("schemathesis.core.shell._DETECTED_SHELL", shell_type)

    case = schema["/users"]["GET"].Case(**case_kwargs)
    command = case.as_curl_command()

    assert command == expected_command
    curl.assert_valid(command)


def test_multipart_with_array_of_bytes_body(curl):
    # When the body contains an array with bytes values (e.g., multiple file uploads)
    multipart_schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "files": {
                                                "type": "array",
                                                "items": {"type": "string", "format": "binary"},
                                            }
                                        },
                                        "required": ["files"],
                                    }
                                }
                            },
                            "required": True,
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )
    multipart_schema.config.update(base_url="http://127.0.0.1")
    operation = multipart_schema["/upload"]["POST"]
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={ParameterLocation.BODY: ComponentInfo(mode=GenerationMode.POSITIVE)},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description="",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        ),
    )
    meta.mark_dirty(ParameterLocation.BODY)
    case = operation.Case(
        body={"files": [b"\x89PNG\r\n\x1a\n", b"\x89PNG\r\n\x1a\n"]},
        media_type="multipart/form-data",
        _meta=meta,
    )
    # Then as_curl_command should not raise an error
    command = case.as_curl_command()
    curl.assert_valid(command)
