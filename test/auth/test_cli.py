import pytest

TOKEN = "FOO"
AUTH_PROVIDER_MODULE_CODE = f"""
import schemathesis

TOKEN = "{TOKEN}"

@schemathesis.auth()
class TokenAuth:
    def get(self, case, context):
        return TOKEN

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}
"""


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_custom_auth(ctx, cli, schema_url, snapshot_cli):
    # When a custom auth is used
    module = ctx.write_pymodule(
        f"""
{AUTH_PROVIDER_MODULE_CODE}
@schemathesis.hook
def after_call(context, case, response):
    assert case.headers["Authorization"] ==  f"Bearer {TOKEN}", case.headers["Authorization"]
    request_authorization = response.request.headers["Authorization"]
    assert request_authorization == f"Bearer {TOKEN}", request_authorization
"""
    )
    # Then CLI should run successfully
    # And the auth should be used
    assert cli.main("run", schema_url, hooks=module) == snapshot_cli


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--auth", "user:pass"), "Basic dXNlcjpwYXNz"),
        (("-H", "Authorization: Bearer EXPLICIT"), "Bearer EXPLICIT"),
    ],
)
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_explicit_auth_precedence(ctx, cli, schema_url, args, expected, snapshot_cli):
    # If explicit auth is passed via CLI
    module = ctx.write_pymodule(
        f"""
{AUTH_PROVIDER_MODULE_CODE}
@schemathesis.hook
def after_call(context, case, response):
    request_authorization = response.request.headers["Authorization"]
    assert request_authorization == "{expected}", request_authorization
"""
    )
    # Then it overrides the one from the auth provider
    # And the auth should be used
    assert cli.main("run", schema_url, "--checks=not_a_server_error", *args, hooks=module) == snapshot_cli


def test_multiple_auth_mechanisms_with_explicit_auth(ctx, cli, snapshot_cli, openapi3_base_url):
    # When the schema defines multiple auth mechanisms on the same operation
    # And the user passes an explicit `Authorization` header
    schema_path = ctx.openapi.write_schema(
        {
            "/health": {
                "get": {
                    "summary": "",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
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
        },
        security=[{"bearerAuth": []}, {"basicAuth": []}],
    )
    # Then it should be able to generate requests
    assert (
        cli.run(
            str(schema_path),
            "-H",
            "Authorization: Bearer foo",
            f"--url={openapi3_base_url}",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "custom_format")
def test_multiple_threads(ctx, cli, schema_url, snapshot_cli):
    module = ctx.write_pymodule(
        f"""
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
    # Then CLI should run successfully
    assert (
        cli.main(
            "run",
            schema_url,
            "--workers",
            "2",
            "--max-examples=1",
            "--checks=not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
def test_requests_auth(ctx, cli, schema_url, snapshot_cli):
    # When the user registers auth from `requests`
    expected = "Basic dXNlcjpwYXNz"
    module = ctx.write_pymodule(
        f"""
from requests.auth import HTTPBasicAuth

schemathesis.auth.set_from_requests(HTTPBasicAuth("user", "pass")).apply_to(method="GET", path="/success")

@schemathesis.hook
def after_call(context, case, response):
    request_authorization = response.request.headers.get("Authorization")
    if case.operation.path == "/success":
        assert request_authorization == "{expected}", request_authorization
    if case.operation.path == "/text":
        assert request_authorization is None, request_authorization
"""
    )
    # Then CLI should run successfully
    # And the auth should be used
    assert cli.main("run", schema_url, "--checks=not_a_server_error", "--mode=positive", hooks=module) == snapshot_cli


@pytest.fixture
def verify_auth(ctx):
    with ctx.check("""
@schemathesis.check
def verify_auth(ctx, response, case):
    request_authorization = response.request.headers.get("Authorization")
    if case.operation.path == "/text":
        expected = f"Bearer {TOKEN_1}"
    if case.operation.path == "/success":
        expected = f"Bearer {TOKEN_2}"
    assert request_authorization == expected, f"Expected `{expected}`, got `{request_authorization}`"
    """) as module:
        yield module


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
@pytest.mark.usefixtures("verify_auth")
def test_conditional(ctx, cli, schema_url, snapshot_cli):
    # When the user sets up multiple auths applied to different API operations
    with ctx.check("""
TOKEN_1 = "ABC"

@schemathesis.auth().apply_to(method="GET", path="/text")
class TokenAuth1:
    def get(self, case, context):
        return TOKEN_1

    def set(self, case, data, context):
        case.headers = {"Authorization": f"Bearer {data}"}


TOKEN_2 = "DEF"

@schemathesis.auth().apply_to(method="GET", path="/success")
class TokenAuth2:
    def get(self, case, context):
        return TOKEN_2

    def set(self, case, data, context):
        case.headers = {"Authorization": f"Bearer {data}"}


@schemathesis.check
def verify_auth(ctx, response, case):
    request_authorization = response.request.headers.get("Authorization")
    if case.operation.path == "/text":
        expected = f"Bearer {TOKEN_1}"
    if case.operation.path == "/success":
        expected = f"Bearer {TOKEN_2}"
    assert request_authorization == expected, f"Expected `{expected}`, got `{request_authorization}`"
    """) as module:
        # Then all auths should be properly applied
        assert cli.main("run", schema_url, "-c", "verify_auth", hooks=module) == snapshot_cli
