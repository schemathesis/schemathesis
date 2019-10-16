from textwrap import dedent

import pytest
from click.testing import CliRunner

import schemathesis.cli

from .app import create_app
from .app import make_schema as make_app_schema
from .app import run_server
from .utils import make_schema

pytest_plugins = ["pytester", "aiohttp.pytest_plugin", "pytest_mock"]


def pytest_configure(config):
    config.addinivalue_line("markers", "endpoints(*names): add only specified endpoints to the test application.")


@pytest.fixture()
def app(request):
    """AioHTTP application with configurable endpoints.

    Endpoint names should be passed as positional arguments to `pytest.mark.endpoints` decorator.
    """
    marker = request.node.get_closest_marker("endpoints")
    if marker:
        endpoints = marker.args
    else:
        endpoints = ("success", "failure")
    return create_app(endpoints)


@pytest.fixture()
def server(app, aiohttp_unused_port):
    """Run the app on an unused port."""
    port = aiohttp_unused_port()
    run_server(app, port)
    yield {"port": port}


@pytest.fixture()
def base_url(server):
    """Base URL for the running application."""
    return f"http://127.0.0.1:{server['port']}"


@pytest.fixture()
def schema_url(base_url):
    """URL of the schema of the running application."""
    return f"{base_url}/swagger.yaml"


@pytest.fixture()
def cli(testdir):
    """CLI runner helper.

    Provides in-process execution via `click.CliRunner` and sub-process execution via `pytest.pytester.Testdir`.
    """

    class Runner:
        @staticmethod
        def run_inprocess(*args, **kwargs):
            cli_runner = CliRunner()
            return cli_runner.invoke(schemathesis.cli.run, args, **kwargs)

        @staticmethod
        def run_subprocess(*args, **kwargs):
            return testdir.run("schemathesis", *args, **kwargs)

    return Runner()


@pytest.fixture
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


@pytest.fixture()
def swagger_20(simple_schema):
    return schemathesis.from_dict(simple_schema)


@pytest.fixture()
def app_schema():
    return make_app_schema(endpoints=("success", "failure"))


@pytest.fixture()
def testdir(testdir):
    def maker(content, method=None, endpoint=None, pytest_plugins=("aiohttp.pytest_plugin",), **kwargs):
        schema = make_schema(**kwargs)
        preparation = dedent(
            """
        import pytest
        import schemathesis
        from test.utils import *
        from hypothesis import settings
        raw_schema = {schema}
        schema = schemathesis.from_dict(raw_schema, method={method}, endpoint={endpoint})
        """.format(
                schema=schema, method=repr(method), endpoint=repr(endpoint)
            )
        )
        testdir.makepyfile(preparation, content)
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

    testdir.make_test = maker

    def run_and_assert(*args, **kwargs):
        result = testdir.runpytest(*args)
        result.assert_outcomes(**kwargs)

    testdir.run_and_assert = run_and_assert

    return testdir
