import io
import os
import shlex
from dataclasses import dataclass, field
from textwrap import dedent
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
import requests
import yaml
from click.testing import CliRunner
from hypothesis import settings
from packaging import version
from urllib3 import HTTPResponse

import schemathesis.cli
from schemathesis import Case
from schemathesis._compat import IS_HYPOTHESIS_ABOVE_6_54, metadata
from schemathesis.cli import reset_checks
from schemathesis.constants import HOOKS_MODULE_ENV_VAR
from schemathesis.experimental import GLOBAL_EXPERIMENTS
from schemathesis.extra._aiohttp import run_server as run_aiohttp_server
from schemathesis.extra._flask import run_server as run_flask_server
from schemathesis.service import HOSTS_PATH_ENV_VAR
from schemathesis.specs.openapi import loaders as oas_loaders
from schemathesis.utils import WSGIResponse

from .apps import _graphql as graphql
from .apps import openapi
from .apps.openapi.schema import OpenAPIVersion, Operation
from .utils import get_schema_path, make_schema

pytest_plugins = ["pytester", "aiohttp.pytest_plugin", "pytest_mock"]


# Register Hypothesis profile. Could be used as
# `pytest test -m hypothesis --hypothesis-profile <profile-name>`
settings.register_profile("CI", max_examples=1000)


@pytest.fixture(autouse=True)
def setup(tmp_path_factory):
    # Avoid failing tests if the local schemathesis CLI is already authenticated with SaaS
    config_dir = tmp_path_factory.mktemp(basename="schemathesis-config")
    hosts_path = config_dir / "hosts.toml"
    hosts_path.touch(exist_ok=True)
    os.environ[HOSTS_PATH_ENV_VAR] = str(hosts_path)


@pytest.fixture(autouse=True)
def reset_hooks():
    GLOBAL_EXPERIMENTS.disable_all()
    schemathesis.hooks.unregister_all()
    schemathesis.auth.unregister()
    reset_checks()
    yield
    GLOBAL_EXPERIMENTS.disable_all()
    schemathesis.hooks.unregister_all()
    schemathesis.auth.unregister()
    reset_checks()


@pytest.fixture(scope="session")
def is_hypothesis_above_6_54():
    return IS_HYPOTHESIS_ABOVE_6_54


@pytest.fixture(scope="session")
def hypothesis_max_examples():
    """Take `max_examples` value if it is not default.

    If it is default, then return None, so individual tests can use appropriate values.
    """
    value = settings().max_examples
    return None if value == 100 else value  # Hypothesis uses 100 examples by default


def pytest_collection_modifyitems(session, config, items):
    """Add the `hypothesis_nested` marker to tests, that depend on the `hypothesis_max_examples` fixture.

    During scheduled test runs on CI, we select such tests and run them with a higher number of examples.
    """
    for item in items:
        if isinstance(item, pytest.Function) and "hypothesis_max_examples" in item.fixturenames:
            item.add_marker("hypothesis_nested")


def pytest_generate_tests(metafunc):
    # A more ergonomic way to limit test parametrization to the specific Open API versions:
    #
    #     @pytest.mark.openapi_version("2.0")
    #
    #  or:
    #
    #     @pytest.mark.openapi_version("2.0", "3.0")
    if "openapi_version" in metafunc.fixturenames:
        marker = metafunc.definition.get_closest_marker("openapi_version")
        if marker is not None:
            variants = [OpenAPIVersion(variant) if isinstance(variant, str) else variant for variant in marker.args]
        else:
            variants = [OpenAPIVersion("2.0"), OpenAPIVersion("3.0")]
        metafunc.parametrize("openapi_version", variants)


def pytest_configure(config):
    config.addinivalue_line("markers", "operations(*names): Add only specified API operations to the test application.")
    config.addinivalue_line("markers", "service(**kwargs): Setup mock server for Schemathesis.io.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
    config.addinivalue_line(
        "markers",
        "openapi_version(*versions): Restrict test parametrization only to the specified Open API version(s).",
    )


@pytest.fixture(scope="session")
def _app():
    """A global AioHTTP application with configurable API operations."""
    return openapi._aiohttp.create_app(("success", "failure"))


@pytest.fixture
def operations(request):
    marker = request.node.get_closest_marker("operations")
    if marker:
        if marker.args and marker.args[0] == "__all__":
            operations = tuple(item for item in Operation.__members__ if item != "all")
        else:
            operations = marker.args
    else:
        operations = ("success", "failure")
    return operations


@pytest.fixture
def reset_app(_app, operations):
    def inner(version):
        openapi._aiohttp.reset_app(_app, operations, version)

    return inner


@pytest.fixture
def app(openapi_version, _app, reset_app):
    """Set up the global app for a specific test.

    NOTE. It might cause race conditions when `pytest-xdist` is used, but they have very low probability.
    """
    reset_app(openapi_version)
    return _app


@pytest.fixture
def open_api_3():
    return OpenAPIVersion("3.0")


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
def openapi2_schema_url(server, openapi_2_app):
    """URL of the schema of the running application."""
    return f"http://127.0.0.1:{server['port']}/schema.yaml"


@pytest.fixture()
def openapi3_schema_url(server, openapi_3_app):
    """URL of the schema of the running application."""
    return f"http://127.0.0.1:{server['port']}/schema.yaml"


@pytest.fixture()
def openapi3_schema(openapi3_schema_url):
    return oas_loaders.from_uri(openapi3_schema_url)


@pytest.fixture
def graphql_path():
    return "/graphql"


@pytest.fixture
def graphql_app(graphql_path):
    return graphql._flask.create_app(graphql_path)


@pytest.fixture()
def graphql_server(graphql_app):
    port = run_flask_server(graphql_app)
    yield {"port": port}


@pytest.fixture()
def graphql_url(graphql_server, graphql_path):
    return f"http://127.0.0.1:{graphql_server['port']}{graphql_path}"


@pytest.fixture()
def graphql_schema(graphql_url):
    return schemathesis.graphql.from_url(graphql_url)


@pytest.fixture
def graphql_strategy(graphql_schema):
    return graphql_schema["/graphql"]["POST"].as_strategy()


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
        def main(*args, hooks=None, **kwargs):
            if hooks is not None:
                env = kwargs.setdefault("env", {})
                env[HOOKS_MODULE_ENV_VAR] = hooks
            return cli_runner.invoke(schemathesis.cli.schemathesis, args, **kwargs)

        @property
        def auth(self):
            return Auth()

    class Auth:
        @staticmethod
        def login(*args, **kwargs):
            return cli_runner.invoke(schemathesis.cli.login, args, **kwargs)

        @staticmethod
        def logout(*args, **kwargs):
            return cli_runner.invoke(schemathesis.cli.logout, args, **kwargs)

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
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture
def empty_open_api_2_schema():
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {},
    }


@pytest.fixture
def empty_open_api_3_schema():
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {},
    }


@pytest.fixture
def open_api_3_schema_with_recoverable_errors(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/foo": {"$ref": "#/components/UnknownMethods"},
        "/bar": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "parameters": [{"$ref": "#/components/UnknownParameter"}],
                "responses": {"200": {"description": "OK"}},
            },
        },
    }
    return empty_open_api_3_schema


@pytest.fixture
def open_api_3_schema_with_yaml_payload(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/yaml": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "text/yaml": {
                            "schema": {"type": "array", "items": {"enum": [42]}, "minItems": 1, "maxItems": 1}
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            },
        },
    }
    return empty_open_api_3_schema


@pytest.fixture
def openapi_3_schema_with_invalid_security(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/data": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"type": "integer"}},
                    },
                },
                "responses": {"200": {"description": "OK"}},
            },
        },
    }
    empty_open_api_3_schema["components"] = {
        "securitySchemes": {
            "bearerAuth": {
                # Missing `type` key
                "scheme": "bearer",
                "bearerFormat": "uuid",
            },
        }
    }
    empty_open_api_3_schema["security"] = [{"bearerAuth": []}]
    return empty_open_api_3_schema


@pytest.fixture
def openapi_3_schema_with_xml(empty_open_api_3_schema):
    id_schema = {"type": "integer", "enum": [42]}

    def operation(schema: dict):
        return {
            "post": {
                "requestBody": {"content": {"application/xml": {"schema": schema}}, "required": True},
                "responses": {"200": {"description": "OK"}},
            }
        }

    def make_object(id_extra=None, **kwargs):
        return {
            "type": "object",
            "properties": {"id": {**id_schema, **(id_extra or {})}},
            "required": ["id"],
            "additionalProperties": False,
            **kwargs,
        }

    def make_array(items, **kwargs):
        return {"type": "array", "items": items, "minItems": 2, "maxItems": 2, **kwargs}

    # No `xml` attributes are used. The default behavior
    no_xml_object = make_object()
    #
    renamed_property_xml_object = make_object(id_extra={"xml": {"name": "renamed-id"}})
    #
    property_as_attribute = make_object(id_extra={"xml": {"attribute": True}})

    simple_array = make_array(items=id_schema)
    wrapped_array = make_array(items=id_schema, xml={"wrapped": True})
    array_with_renaming = make_array(
        items={**id_schema, "xml": {"name": "item"}}, xml={"wrapped": True, "name": "items-array"}
    )
    object_in_array = make_array(
        items=make_object(id_extra={"xml": {"name": "item-id"}}, xml={"name": "item"}),
        xml={"wrapped": True, "name": "items"},
    )
    array_in_object = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {**id_schema, "xml": {"name": "id"}},
                "minItems": 2,
                "maxItems": 2,
                "xml": {"wrapped": True, "name": "items-array"},
            },
        },
        "required": ["items"],
        "additionalProperties": False,
        "xml": {"name": "items-object"},
    }

    prefixed_object = make_object(xml={"prefix": "smp"})
    prefixed_array = make_array(items=id_schema, xml={"prefix": "smp", "namespace": "http://example.com/schema"})
    prefixed_attribute = make_object(
        id_extra={"xml": {"attribute": True, "prefix": "smp", "namespace": "http://example.com/schema"}}
    )
    namespaced_object = make_object(xml={"namespace": "http://example.com/schema"})
    namespaced_array = make_array(items=id_schema, xml={"namespace": "http://example.com/schema"})
    namespaced_wrapped_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "wrapped": True}
    )
    namespaced_prefixed_object = make_object(xml={"namespace": "http://example.com/schema", "prefix": "smp"})
    namespaced_prefixed_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "prefix": "smp"}
    )
    namespaced_prefixed_wrapped_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "prefix": "smp", "wrapped": True}
    )

    empty_open_api_3_schema["components"] = {
        "schemas": {
            # This name is used in XML
            "AutoName": no_xml_object,
            "ExplicitName": {**no_xml_object, "xml": {"name": "CustomName"}},
            "RenamedProperty": renamed_property_xml_object,
            "PropertyAsAttribute": property_as_attribute,
            "SimpleArray": simple_array,
            "WrappedArray": wrapped_array,
            "ArrayWithRenaming": array_with_renaming,
            "ObjectInArray": object_in_array,
            "ArrayInObject": array_in_object,
            "PrefixedObject": prefixed_object,
            "PrefixedArray": prefixed_array,
            "PrefixedAttribute": prefixed_attribute,
            "NamespacedObject": namespaced_object,
            "NamespacedArray": namespaced_array,
            "NamespacedWrappedArray": namespaced_wrapped_array,
            "NamespacedPrefixedObject": namespaced_prefixed_object,
            "NamespacedPrefixedArray": namespaced_prefixed_array,
            "NamespacedPrefixedWrappedArray": namespaced_prefixed_wrapped_array,
        }
    }
    empty_open_api_3_schema["paths"] = {
        "/root-name": operation(make_object()),
        "/auto-name": operation({"$ref": "#/components/schemas/AutoName"}),
        "/explicit-name": operation({"$ref": "#/components/schemas/ExplicitName"}),
        "/renamed-property": operation({"$ref": "#/components/schemas/RenamedProperty"}),
        "/property-attribute": operation({"$ref": "#/components/schemas/PropertyAsAttribute"}),
        "/simple-array": operation({"$ref": "#/components/schemas/SimpleArray"}),
        "/wrapped-array": operation({"$ref": "#/components/schemas/WrappedArray"}),
        "/array-with-renaming": operation({"$ref": "#/components/schemas/ArrayWithRenaming"}),
        "/object-in-array": operation({"$ref": "#/components/schemas/ObjectInArray"}),
        "/array-in-object": operation({"$ref": "#/components/schemas/ArrayInObject"}),
        "/prefixed-object": operation({"$ref": "#/components/schemas/PrefixedObject"}),
        "/prefixed-array": operation({"$ref": "#/components/schemas/PrefixedArray"}),
        "/prefixed-attribute": operation({"$ref": "#/components/schemas/PrefixedAttribute"}),
        "/namespaced-object": operation({"$ref": "#/components/schemas/NamespacedObject"}),
        "/namespaced-array": operation({"$ref": "#/components/schemas/NamespacedArray"}),
        "/namespaced-wrapped-array": operation({"$ref": "#/components/schemas/NamespacedWrappedArray"}),
        "/namespaced-prefixed-object": operation({"$ref": "#/components/schemas/NamespacedPrefixedObject"}),
        "/namespaced-prefixed-array": operation({"$ref": "#/components/schemas/NamespacedPrefixedArray"}),
        "/namespaced-prefixed-wrapped-array": operation(
            {"$ref": "#/components/schemas/NamespacedPrefixedWrappedArray"}
        ),
    }
    return empty_open_api_3_schema


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
                                    "example": {"key": "foo"},
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


@pytest.fixture
def schema_with_recursive_references():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "required": ["child"],
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
        "paths": {
            "/foo": {
                "post": {
                    "summary": "Test",
                    "description": "",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {},
                        }
                    },
                }
            }
        },
    }


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
def app_schema(openapi_version, operations):
    return openapi._aiohttp.make_openapi_schema(operations=operations, version=openapi_version)


@pytest.fixture()
def testdir(testdir):
    def maker(
        content,
        method=None,
        endpoint=None,
        tag=None,
        pytest_plugins=("aiohttp.pytest_plugin",),
        validate_schema=True,
        sanitize_output=True,
        schema=None,
        schema_name="simple_swagger.yaml",
        **kwargs,
    ):
        schema = schema or make_schema(schema_name=schema_name, **kwargs)
        preparation = dedent(
            """
        import pytest
        import schemathesis
        from schemathesis.stateful import Stateful
        from schemathesis.utils import NOT_SET
        from schemathesis import DataGenerationMethod
        from test.utils import *
        from hypothesis import given, settings, HealthCheck, Phase, assume, strategies as st, seed
        raw_schema = {schema}

        note = print  # An alias to distinguish with leftover prints

        @pytest.fixture
        def simple_schema():
            return schema

        schema = schemathesis.from_dict(
            raw_schema,
            method={method},
            endpoint={endpoint},
            tag={tag},
            validate_schema={validate_schema},
            sanitize_output={sanitize_output}
        )
        """.format(
                schema=schema,
                method=repr(method),
                endpoint=repr(endpoint),
                tag=repr(tag),
                validate_schema=repr(validate_schema),
                sanitize_output=repr(sanitize_output),
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


@pytest.fixture
def wsgi_app_factory():
    return openapi._flask.create_app


@pytest.fixture()
def flask_app(wsgi_app_factory, operations):
    return wsgi_app_factory(operations)


@pytest.fixture
def asgi_app_factory():
    return openapi._fastapi.create_app


@pytest.fixture()
def fastapi_app(asgi_app_factory):
    return asgi_app_factory()


@pytest.fixture()
def fastapi_graphql_app(graphql_path):
    return graphql._fastapi.create_app(graphql_path)


@pytest.fixture
def real_app_schema(schema_url):
    return oas_loaders.from_uri(schema_url)


@pytest.fixture
def wsgi_app_schema(schema_url, flask_app):
    return oas_loaders.from_wsgi("/schema.yaml", flask_app)


@pytest.fixture(params=["real_app_schema", "wsgi_app_schema"])
def any_app_schema(openapi_version, request):
    return request.getfixturevalue(request.param)


def make_importable(module):
    """Make the package importable by the inline CLI execution."""
    pkgroot = module.dirpath()
    module._ensuresyspath(True, pkgroot)


@pytest.fixture
def loadable_flask_app(testdir, operations):
    module = testdir.make_importable_pyfile(
        location=f"""
        from test.apps.openapi._flask import create_app

        app = create_app({operations})
        """
    )
    return f"{module.purebasename}:app"


@pytest.fixture
def loadable_aiohttp_app(testdir, operations, openapi_version):
    module = testdir.make_importable_pyfile(
        location=f"""
        from test.apps.openapi._aiohttp import create_app

        app = create_app({operations})
        """
    )
    return f"{module.purebasename}:app"


@pytest.fixture
def loadable_graphql_fastapi_app(testdir, graphql_path):
    module = testdir.make_importable_pyfile(
        location=f"""
        from test.apps._graphql._fastapi import create_app

        app = create_app('{graphql_path}')
        """
    )
    return f"{module.purebasename}:app"


@pytest.fixture(scope="session")
def is_older_subtests():
    # For compatibility needs
    version_string = metadata.version("pytest_subtests")
    return version.parse(version_string) < version.parse("0.6.0")


@pytest.fixture
def response_factory():
    def requests_factory(
        *,
        content: bytes = b"{}",
        content_type: Optional[str] = "application/json",
        status_code: int = 200,
        headers: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        response = requests.Response()
        response._content = content
        response.status_code = status_code
        headers = headers or {}
        if content_type:
            headers.setdefault("Content-Type", content_type)
        response.headers.update(headers)
        response.raw = HTTPResponse(body=io.BytesIO(content), status=status_code, headers=response.headers)
        response.request = requests.PreparedRequest()
        response.request.prepare(method="POST", url="http://127.0.0.1", headers=headers)
        return response

    def werkzeug_factory(*, status_code: int = 200, headers: Optional[Dict[str, Any]] = None):
        response = WSGIResponse(response=b'{"some": "value"}', status=status_code)
        response.request = requests.PreparedRequest()
        response.request.prepare(
            method="POST", url="http://example.com", headers={"Content-Type": "application/json", **(headers or {})}
        )
        return response

    return SimpleNamespace(
        requests=requests_factory,
        werkzeug=werkzeug_factory,
    )


@pytest.fixture
def case_factory(swagger_20):
    def factory(**kwargs):
        kwargs.setdefault("operation", swagger_20["/users"]["get"])
        return Case(**kwargs)

    return factory


@dataclass
class CurlWrapper:
    testdir: field()

    def run(self, command: str):
        return self.testdir.run(*shlex.split(command))

    def assert_valid(self, command: str):
        result = self.run(command)
        if result.ret != 0:
            # The command is valid, but the target is not reachable
            assert "Failed to connect" in result.stderr.lines[-1]


@pytest.fixture
def curl(testdir):
    return CurlWrapper(testdir)
