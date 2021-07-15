import pytest


@pytest.fixture(params=["real", "wsgi"])
def app_type(request):
    return request.param


@pytest.fixture
def cli_args(request, openapi_version, app_type):
    if app_type == "real":
        schema_url = request.getfixturevalue("schema_url")
        args = (schema_url,)
    else:
        app_path = request.getfixturevalue("loadable_flask_app")
        args = (f"--app={app_path}", "/schema.yaml")
    return args
