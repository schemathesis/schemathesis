from textwrap import dedent

import pytest
from aiohttp.test_utils import unused_port
from click.testing import CliRunner

import schemathesis.cli

from .app import create_app
from .app import make_schema as make_app_schema
from .app import reset_app, run_server
from .utils import make_schema

pytest_plugins = ["pytester", "aiohttp.pytest_plugin", "pytest_mock"]


def pytest_configure(config):
    config.addinivalue_line("markers", "endpoints(*names): add only specified endpoints to the test application.")


@pytest.fixture(scope="session")
def _app():
    """A global AioHTTP application with configurable endpoints."""
    return create_app(("success", "failure"))


@pytest.fixture()
def app(_app, request):
    """Set up the global app for a specific test.

    NOTE. It might cause race conditions when `pytest-xdist` is used, but they have very low probability.
    """
    marker = request.node.get_closest_marker("endpoints")
    if marker:
        endpoints = marker.args
    else:
        endpoints = ("success", "failure")
    reset_app(_app, endpoints)
    return _app


@pytest.fixture(scope="session")
def server(_app):
    """Run the app on an unused port."""
    port = unused_port()
    run_server(_app, port)
    yield {"port": port}


@pytest.fixture()
def base_url(server, app):
    """Base URL for the running application."""
    return f"http://127.0.0.1:{server['port']}"


@pytest.fixture()
def schema_url(base_url):
    """URL of the schema of the running application."""
    return f"{base_url}/swagger.yaml"


@pytest.fixture(scope="session")
def cli():
    """CLI runner helper.

    Provides in-process execution via `click.CliRunner` and sub-process execution via `pytest.pytester.Testdir`.
    """

    cli_runner = CliRunner()

    class Runner:
        @staticmethod
        def run(*args, **kwargs):
            return cli_runner.invoke(schemathesis.cli.run, args, **kwargs)

        @staticmethod
        def main(*args, **kwargs):
            return cli_runner.invoke(schemathesis.cli.schemathesis, args, **kwargs)

    return Runner()


@pytest.fixture(scope="session")
def simple_schema():
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/users": {
                "get": {
                    "summary": "Returns a list of users.",
                    "description": "Optional extended description in Markdown.",
                    "produces": ["application/json"],
                    "responses": {200: {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="session")
def swagger_20(simple_schema):
    return schemathesis.from_dict(simple_schema)


@pytest.fixture(scope="session")
def openapi_30():
    raw = make_schema("simple_openapi.yaml")
    return schemathesis.from_dict(raw)


@pytest.fixture()
def app_schema():
    return make_app_schema(endpoints=("success", "failure"))


@pytest.fixture()
def testdir(testdir):
    def maker(content, method=None, endpoint=None, tag=None, pytest_plugins=("aiohttp.pytest_plugin",), **kwargs):
        schema = make_schema(**kwargs)
        preparation = dedent(
            """
        import pytest
        import schemathesis
        from test.utils import *
        from hypothesis import given, settings
        raw_schema = {schema}
        schema = schemathesis.from_dict(raw_schema, method={method}, endpoint={endpoint}, tag={tag})
        """.format(
                schema=schema, method=repr(method), endpoint=repr(endpoint), tag=repr(tag)
            )
        )
        module = testdir.makepyfile(preparation, content)
        testdir.makepyfile(
            conftest=dedent(
                f"""
        pytest_plugins = {pytest_plugins}
        def pytest_configure(config):
            config.HYPOTHESIS_CASES = 0
        def pytest_unconfigure(config):
            print(f"Hypothesis calls: {{config.HYPOTHESIS_CASES}}")
        """
            )
        )
        return module

    testdir.make_test = maker

    def run_and_assert(*args, **kwargs):
        result = testdir.runpytest(*args)
        result.assert_outcomes(**kwargs)

    testdir.run_and_assert = run_and_assert

    return testdir
