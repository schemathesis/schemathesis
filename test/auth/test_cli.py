import json
import sys

import pytest
from _pytest.main import ExitCode

TOKEN = "FOO"
AUTH_PROVIDER_MODULE_CODE = f"""
import schemathesis

TOKEN = "{TOKEN}"

note = print

@schemathesis.auth()
class TokenAuth:
    def get(self, case, context):
        return TOKEN

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}
"""


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_custom_auth(testdir, cli, schema_url, app):
    # When a custom auth is used
    module = testdir.make_importable_pyfile(
        hook=f"""
{AUTH_PROVIDER_MODULE_CODE}
@schemathesis.hook
def after_call(context, case, response):
    assert case.headers["Authorization"] ==  f"Bearer {TOKEN}", case.headers["Authorization"]
    request_authorization = response.request.headers["Authorization"]
    assert request_authorization == f"Bearer {TOKEN}", request_authorization
    note()
    note(request_authorization)
    note()
"""
    )
    result = cli.main("run", schema_url, hooks=module.purebasename)
    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the auth should be used
    assert f"Bearer {TOKEN}" in result.stdout.splitlines()


@pytest.mark.parametrize(
    "args, expected",
    (
        (("--auth", "user:pass"), "Basic dXNlcjpwYXNz"),
        (("-H", "Authorization: Bearer EXPLICIT"), "Bearer EXPLICIT"),
    ),
)
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_explicit_auth_precedence(testdir, cli, schema_url, args, expected):
    # If explicit auth is passed via CLI
    module = testdir.make_importable_pyfile(
        hook=f"""
{AUTH_PROVIDER_MODULE_CODE}
@schemathesis.hook
def after_call(context, case, response):
    request_authorization = response.request.headers["Authorization"]
    assert request_authorization == "{expected}", request_authorization
    note()
    note(request_authorization)
    note()
"""
    )
    # Then it overrides the one from the auth provider
    result = cli.main("run", schema_url, "--show-errors-tracebacks", *args, hooks=module.purebasename)
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the auth should be used
    assert expected in result.stdout.splitlines()


def test_multiple_auth_mechanisms_with_explicit_auth(testdir, empty_open_api_3_schema, cli):
    # When the schema defines multiple auth mechanisms on the same operation
    # And the user passes an explicit `Authorization` header
    empty_open_api_3_schema["paths"] = {
        "/health": {
            "get": {
                "summary": "",
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    empty_open_api_3_schema["components"] = {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "uuid",
                "description": '* Thing access: "Authorization: Thing <thing_key>"\n',
            },
            "basicAuth": {
                "type": "http",
                "scheme": "basic",
                "description": '* Things access: "Authorization: Basic <base64-encoded_credentials>"\n',
            },
        }
    }
    empty_open_api_3_schema["security"] = [{"bearerAuth": []}, {"basicAuth": []}]
    schema_file = testdir.makefile(".json", schema=json.dumps(empty_open_api_3_schema))
    result = cli.run(str(schema_file), "--dry-run", "-H", "Authorization: Bearer foo")
    # Then it should be able to generate requests
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "custom_format")
def test_multiple_threads(testdir, cli, schema_url):
    module = testdir.make_importable_pyfile(
        hook=f"""
    import schemathesis
    import time

    TOKEN = "{TOKEN}"

    @schemathesis.auth()
    class TokenAuth:

        def __init__(self):
            self.get_calls = 0

        def get(self, case, context):
            self.get_calls += 1
            time.sleep(0.05)
            return TOKEN

        def set(self, case, data, context):
            case.headers = {{"Authorization": f"Bearer {{data}}"}}

    @schemathesis.hook
    def after_call(context, case, response):
        provider = schemathesis.auths.GLOBAL_AUTH_STORAGE.providers[0].provider
        assert provider.get_calls == 1, provider.get_calls
    """
    )
    result = cli.main(
        "run",
        schema_url,
        "--workers",
        "2",
        "--hypothesis-max-examples=1",
        "--show-errors-tracebacks",
        hooks=module.purebasename,
    )
    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
def test_requests_auth(testdir, cli, schema_url):
    # When the user registers auth from `requests`
    expected = "Basic dXNlcjpwYXNz"
    module = testdir.make_importable_pyfile(
        hook=f"""
import schemathesis

from requests.auth import HTTPBasicAuth

schemathesis.auth.set_from_requests(HTTPBasicAuth("user", "pass")).apply_to(method="GET", path="/success")

note = print

@schemathesis.hook
def after_call(context, case, response):
    request_authorization = response.request.headers.get("Authorization")
    if case.operation.path == "/success":
        assert request_authorization == "{expected}", request_authorization
        note()
        note(request_authorization)
        note()
    if case.operation.path == "/text":
        assert request_authorization is None, request_authorization
"""
    )
    result = cli.main("run", schema_url, hooks=module.purebasename)
    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the auth should be used
    assert expected in result.stdout.splitlines()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
def test_conditional(testdir, cli, schema_url):
    # When the user sets up multiple auths applied to different API operations
    if sys.version_info < (3, 9):
        dec1 = """
auth = schemathesis.auth()
@auth.apply_to(method="GET", path="/text")"""
        dec2 = """
auth = schemathesis.auth()
@auth.apply_to(method="GET", path="/success")"""
    else:
        dec1 = '@schemathesis.auth().apply_to(method="GET", path="/text")'
        dec2 = '@schemathesis.auth().apply_to(method="GET", path="/success")'
    module = testdir.make_importable_pyfile(
        hook=f"""
import schemathesis

TOKEN_1 = "ABC"

{dec1}
class TokenAuth1:
    def get(self, case, context):
        return TOKEN_1

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}


TOKEN_2 = "DEF"

{dec2}
class TokenAuth2:
    def get(self, case, context):
        return TOKEN_2

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}


@schemathesis.check
def verify_auth(response, case):
    request_authorization = response.request.headers.get("Authorization")
    if case.operation.path == "/text":
        expected = f"Bearer {{TOKEN_1}}"
    if case.operation.path == "/success":
        expected = f"Bearer {{TOKEN_2}}"
    assert request_authorization == expected, f"Expected `{{expected}}`, got `{{request_authorization}}`"
"""
    )
    result = cli.main("run", schema_url, "-c", "verify_auth", hooks=module.purebasename)
    # Then all auths should be properly applied
    assert result.exit_code == ExitCode.OK, result.stdout
