from textwrap import dedent

import pytest
import yaml
from click.testing import CliRunner
from hypothesis import settings

import schemathesis.cli
from schemathesis.extra._aiohttp import run_server as run_aiohttp_server
from schemathesis.extra._flask import run_server as run_flask_server

from .apps import Endpoint, _aiohttp, _fastapi, _flask, _graphql
from .apps.utils import OpenAPIVersion
from .utils import get_schema_path, make_schema

pytest_plugins = ["pytester", "aiohttp.pytest_plugin", "pytest_mock"]


# Register Hypothesis profile. Could be used as
# `pytest test -m hypothesis --hypothesis-profile <profile-name>`
settings.register_profile("CI", max_examples=10000)


def pytest_configure(config):
    config.addinivalue_line("markers", "endpoints(*names): add only specified endpoints to the test application.")
    config.addinivalue_line("markers", "hypothesis_nested: mark tests with nested Hypothesis tests.")


@pytest.fixture(scope="session")
def _app():
    """A global AioHTTP application with configurable endpoints."""
    return _aiohttp.create_openapi_app(("success", "failure"))


@pytest.fixture
def endpoints(request):
    marker = request.node.get_closest_marker("endpoints")
    if marker:
        if marker.args and marker.args[0] == "__all__":
            endpoints = tuple(Endpoint.__members__)
        else:
            endpoints = marker.args
    else:
        endpoints = ("success", "failure")
    return endpoints


@pytest.fixture
def reset_app(_app, endpoints):
    def inner(version):
        _aiohttp.reset_app(_app, endpoints, version)

    return inner


@pytest.fixture(params=[OpenAPIVersion("2.0"), OpenAPIVersion("3.0")])
def openapi_version(request):
    return request.param


@pytest.fixture
def app(openapi_version, _app, reset_app):
    """Set up the global app for a specific test.

    NOTE. It might cause race conditions when `pytest-xdist` is used, but they have very low probability.
    """
    reset_app(openapi_version)
    return _app


@pytest.fixture
def openapi_2_app(_app, reset_app):
    reset_app(OpenAPIVersion("2.0"))
    return _app


@pytest.fixture
def openapi_3_app(_app, reset_app):
    reset_app(OpenAPIVersion("3.0"))
    return _app


@pytest.fixture(scope="session")
def server(_app):
    """Run the app on an unused port."""
    port = run_aiohttp_server(_app)
    yield {"port": port}


@pytest.fixture()
def base_url(server, app):
    """Base URL for the running application."""
    return f"http://127.0.0.1:{server['port']}/api"


@pytest.fixture()
def openapi2_base_url(server, openapi_2_app):
    return f"http://127.0.0.1:{server['port']}/api"


@pytest.fixture()
def openapi3_base_url(server, openapi_3_app):
    return f"http://127.0.0.1:{server['port']}/api"


@pytest.fixture()
def schema_url(server, app):
    """URL of the schema of the running application."""
    return f"http://127.0.0.1:{server['port']}/schema.yaml"


@pytest.fixture()
def openapi3_schema_url(server, openapi_3_app):
    """URL of the schema of the running application."""
    return f"http://127.0.0.1:{server['port']}/schema.yaml"


@pytest.fixture(scope="session")
def graphql_path():
    return "/graphql"


@pytest.fixture(scope="session")
def graphql_app(graphql_path):
    return _graphql.create_app(graphql_path)


@pytest.fixture()
def graphql_server(graphql_app):
    port = run_flask_server(graphql_app)
    yield {"port": port}


@pytest.fixture()
def graphql_endpoint(graphql_server, graphql_path):
    return f"http://127.0.0.1:{graphql_server['port']}{graphql_path}"


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
        def replay(*args, **kwargs):
            return cli_runner.invoke(schemathesis.cli.replay, args, **kwargs)

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
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="session")
def simple_openapi():
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/query": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1}},
                        {"name": "value", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="session")
def fast_api_schema():
    # This schema contains definitions from JSON Schema Draft 7
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/query": {
                "get": {
                    "parameters": [
                        {
                            "name": "value",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 10},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="session")
def schema_with_get_payload():
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"key": {"type": "string"}},
                                    "required": ["key"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        },
    }


ROOT_SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
    "paths": {"/teapot": {"$ref": "paths/teapot.yaml#/TeapotCreatePath"}},
}
TEAPOT_PATHS = {
    "TeapotCreatePath": {
        "post": {
            "summary": "Test",
            "requestBody": {
                "description": "Test.",
                "content": {
                    "application/json": {"schema": {"$ref": "../schemas/teapot/create.yaml#/TeapotCreateRequest"}}
                },
                "required": True,
            },
            "responses": {"default": {"$ref": "../../common/responses.yaml#/DefaultError"}},
            "tags": ["ancillaries"],
        }
    }
}
TEAPOT_CREATE_SCHEMAS = {
    "TeapotCreateRequest": {
        "type": "object",
        "description": "Test",
        "additionalProperties": False,
        "properties": {"username": {"type": "string"}, "profile": {"$ref": "#/Profile"}},
        "required": ["username", "profile"],
    },
    "Profile": {
        "type": "object",
        "description": "Test",
        "additionalProperties": False,
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    },
}
COMMON_RESPONSES = {
    "DefaultError": {
        "description": "Probably an error",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "key": {"type": "string", "nullable": True},
                        "referenced": {"$ref": "attributes.yaml#/referenced"},
                    },
                    "required": ["key", "referenced"],
                }
            }
        },
    }
}
ATTRIBUTES = {"referenced": {"$ref": "attributes_nested.yaml#/nested_reference"}}
ATTRIBUTES_NESTED = {"nested_reference": {"type": "string", "nullable": True}}


@pytest.fixture()
def complex_schema(testdir):
    # This schema includes:
    #   - references to other files
    #   - local references in referenced files
    #   - different directories - relative paths to other files
    schema_root = testdir.mkdir("root")
    common = testdir.mkdir("common")
    paths = schema_root.mkdir("paths")
    schemas = schema_root.mkdir("schemas")
    teapot_schemas = schemas.mkdir("teapot")
    root = schema_root / "root.yaml"
    root.write_text(yaml.dump(ROOT_SCHEMA), "utf8")
    (paths / "teapot.yaml").write_text(yaml.dump(TEAPOT_PATHS), "utf8")
    (teapot_schemas / "create.yaml").write_text(yaml.dump(TEAPOT_CREATE_SCHEMAS), "utf8")
    (common / "responses.yaml").write_text(yaml.dump(COMMON_RESPONSES), "utf8")
    (common / "attributes.yaml").write_text(yaml.dump(ATTRIBUTES), "utf8")
    (common / "attributes_nested.yaml").write_text(yaml.dump(ATTRIBUTES_NESTED), "utf8")
    return str(root)


@pytest.fixture(name="get_schema_path")
def _get_schema_path():
    return get_schema_path


@pytest.fixture()
def swagger_20(simple_schema):
    return schemathesis.from_dict(simple_schema)


@pytest.fixture()
def openapi_30():
    raw = make_schema("simple_openapi.yaml")
    return schemathesis.from_dict(raw)


@pytest.fixture()
def app_schema(openapi_version, endpoints):
    return _aiohttp.make_openapi_schema(endpoints=endpoints, version=openapi_version)


@pytest.fixture()
def testdir(testdir):
    def maker(
        content,
        method=None,
        endpoint=None,
        tag=None,
        pytest_plugins=("aiohttp.pytest_plugin",),
        validate_schema=True,
        schema=None,
        schema_name="simple_swagger.yaml",
        **kwargs,
    ):
        schema = schema or make_schema(schema_name=schema_name, **kwargs)
        preparation = dedent(
            """
        import pytest
        import schemathesis
        from schemathesis import Stateful
        from test.utils import *
        from hypothesis import given, settings, HealthCheck, Phase, strategies as st
        raw_schema = {schema}
        schema = schemathesis.from_dict(raw_schema, method={method}, endpoint={endpoint}, tag={tag}, validate_schema={validate_schema})
        """.format(
                schema=schema,
                method=repr(method),
                endpoint=repr(endpoint),
                tag=repr(tag),
                validate_schema=repr(validate_schema),
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

    def make_importable_pyfile(*args, **kwargs):
        module = testdir.makepyfile(*args, **kwargs)
        make_importable(module)
        return module

    testdir.make_importable_pyfile = make_importable_pyfile

    def run_and_assert(*args, **kwargs):
        result = testdir.runpytest(*args)
        result.assert_outcomes(**kwargs)
        return result

    testdir.run_and_assert = run_and_assert

    return testdir


@pytest.fixture()
def flask_app(endpoints):
    return _flask.create_openapi_app(endpoints)


@pytest.fixture()
def fastapi_app():
    return _fastapi.create_app()


def make_importable(module):
    """Make the package importable by the inline CLI execution."""
    pkgroot = module.dirpath()
    module._ensuresyspath(True, pkgroot)


@pytest.fixture
def loadable_flask_app(testdir, endpoints):
    module = testdir.make_importable_pyfile(
        location=f"""
        from test.apps._flask import create_openapi_app

        app = create_openapi_app({endpoints})
        """
    )
    return f"{module.purebasename}:app"


@pytest.fixture
def loadable_aiohttp_app(testdir, endpoints):
    module = testdir.make_importable_pyfile(
        location=f"""
        from test.apps._aiohttp import create_openapi_app

        app = create_openapi_app({endpoints})
        """
    )
    return f"{module.purebasename}:app"
