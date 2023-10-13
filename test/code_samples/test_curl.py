from test.apps.openapi._fastapi.app import app

import pytest

import schemathesis
from schemathesis import Case

schema = schemathesis.from_dict(app.openapi(), force_schema_version="30")


@pytest.mark.parametrize("headers", (None, {"X-Key": "42"}))
@schema.parametrize()
def test_as_curl_command(case: Case, headers):
    command = case.as_curl_command(headers)
    expected_headers = "" if not headers else " ".join(f" -H '{name}: {value}'" for name, value in headers.items())
    assert command == f"curl -X GET{expected_headers} http://localhost/users"


def test_non_utf_8_body():
    case = Case(operation=schema["/users"]["GET"], body=b"42\xff", media_type="application/octet-stream")
    command = case.as_curl_command()
    assert command == "curl -X GET -d '42�' http://localhost/users"


def test_explicit_headers():
    # When the generated case contains a header from the list of headers that are ignored by default
    name = "Accept"
    value = "application/json"
    case = Case(operation=schema["/users"]["GET"], headers={name: value})
    command = case.as_curl_command()
    assert command == f"curl -X GET -H '{name}: {value}' http://localhost/users"


@pytest.mark.operations("failure")
@pytest.mark.openapi_version("3.0")
def test_cli_output(cli, base_url, schema_url):
    result = cli.run(schema_url, "--code-sample-style=curl")
    lines = result.stdout.splitlines()
    assert "Run this cURL command to reproduce this failure: " in lines
    assert f"    curl -X GET {base_url}/failure" in lines


@pytest.mark.operations("failure")
@pytest.mark.openapi_version("3.0")
def test_cli_output_includes_insecure(cli, base_url, schema_url):
    result = cli.run(schema_url, "--code-sample-style=curl", "--request-tls-verify=false")
    lines = result.stdout.splitlines()
    assert f"    curl -X GET --insecure {base_url}/failure" in lines


@pytest.mark.operations("failure")
def test_pytest_subtests_output(testdir, openapi3_base_url, app_schema):
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"
lazy_schema = schemathesis.from_pytest_fixture("simple_schema", code_sample_style="curl")

@lazy_schema.parametrize()
def test_(case):
    case.call_and_validate()
""",
        schema=app_schema,
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [r"E +Run this cURL command to reproduce this response:", rf"E + curl -X GET {openapi3_base_url}/failure"]
    )
