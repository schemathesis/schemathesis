import pytest
from _pytest.main import ExitCode

import schemathesis


@pytest.fixture(autouse=True)
def unregister_hooks():
    yield
    schemathesis.hooks.unregister_all()


TOKEN = "FOO"
AUTH_PROVIDER_MODULE_CODE = f"""
import schemathesis

TOKEN = "{TOKEN}"

note = print

@schemathesis.auth()
class TokenAuth:
    def get(self, context):
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

        def get(self, context):
            self.get_calls += 1
            time.sleep(0.05)
            return TOKEN

        def set(self, case, data, context):
            case.headers = {{"Authorization": f"Bearer {{data}}"}}

    @schemathesis.hook
    def after_call(context, case, response):
        provider = schemathesis.auths.GLOBAL_AUTH_STORAGE.provider.provider
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
