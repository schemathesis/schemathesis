from __future__ import annotations

import datetime
import io
import logging
import shlex
from dataclasses import dataclass, field
from textwrap import dedent
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import requests
import yaml
from hypothesis import settings
from urllib3 import HTTPResponse
from werkzeug import Request
from werkzeug.datastructures import Headers
from werkzeug.test import TestResponse

from schemathesis.cli.commands.run.handlers import output
from schemathesis.core.transport import Response

from .utils import make_schema

pytest_plugins = [
    "pytester",
    "pytest_mock",
    "test.fixtures.ctx",
    "test.fixtures.app_runner",
    "test.fixtures.snapshots",
    "test.fixtures.reset",
    "test.fixtures.markers",
    "test.fixtures.cli",
]

logging.getLogger("pyrate_limiter").setLevel(logging.CRITICAL)

# Register Hypothesis profile. Could be used as
# `pytest test -m hypothesis --hypothesis-profile <profile-name>`
settings.register_profile("CI", max_examples=2000)

output.SCHEMATHESIS_VERSION = "dev"


@pytest.fixture(scope="session")
def hypothesis_max_examples():
    # Returns max_examples when overridden via `--hypothesis-profile`, else None so each test can pick its own.
    value = settings().max_examples
    return None if value == 100 else value


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
def open_api_3_schema_with_recoverable_errors(ctx):
    return ctx.openapi.build_schema(
        {
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
    )


@pytest.fixture
def open_api_3_schema_with_yaml_payload(ctx):
    return ctx.openapi.load_schema(
        {
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
    )


@pytest.fixture
def openapi_3_schema_with_invalid_security(ctx):
    return ctx.openapi.build_schema(
        {
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
        },
        components={
            "securitySchemes": {
                "bearerAuth": {
                    # Missing `type` key
                    "scheme": "bearer",
                    "bearerFormat": "uuid",
                },
            }
        },
        security=[{"bearerAuth": []}],
    )


@pytest.fixture
def openapi_3_schema_with_xml(ctx):
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
    renamed_property_xml_object = make_object(id_extra={"xml": {"name": "renamed-id"}})
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

    return ctx.openapi.load_schema(
        {
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
        },
        components={
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
        },
    )


@pytest.fixture
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


@pytest.fixture
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


@pytest.fixture
def swagger_20(ctx, simple_schema):
    return ctx.openapi.from_full_schema(simple_schema)


@pytest.fixture
def openapi_30(ctx):
    return ctx.openapi.from_full_schema(make_schema("simple_openapi.yaml"))


@pytest.fixture
def openapi_31(ctx):
    raw = make_schema("simple_openapi.yaml")
    raw["openapi"] = "3.1.0"
    return ctx.openapi.from_full_schema(raw)


@pytest.fixture
def testdir(testdir):
    def maker(
        content,
        pytest_plugins=(),
        sanitize_output=True,
        generation_modes=None,
        schema=None,
        schema_name="simple_swagger.yaml",
        **kwargs,
    ):
        schema = schema or make_schema(schema_name=schema_name, **kwargs)
        modes = (
            "[" + ", ".join([f"GenerationMode.{m.value.upper()}" for m in generation_modes]) + "]"
            if generation_modes
            else None
        )
        preparation = dedent(
            f"""
        import pytest
        import schemathesis
        from schemathesis.core import NOT_SET
        from schemathesis.config import *
        from schemathesis.generation import GenerationMode
        from test.utils import *
        from hypothesis import given, settings, HealthCheck, Phase, assume, strategies as st, seed
        raw_schema = {schema}

        note = print  # An alias to distinguish with leftover prints

        @pytest.fixture
        def simple_schema():
            return schema

        config = SchemathesisConfig()
        config.output.sanitization.update(enabled={sanitize_output!r})

        schema = schemathesis.openapi.from_dict(
            raw_schema, config=config
        )

        if {modes} is not None:
            schema.config.generation.update(modes={modes})
        """
        )
        module = testdir.makepyfile(preparation, content)
        testdir.makepyfile(
            conftest=dedent(
                f"""
        pytest_plugins = {pytest_plugins}
        def pytest_configure(config):
            config.HYPOTHESIS_CASES = 0
            config.addinivalue_line(
                "filterwarnings",
                "ignore:Unclosed <MemoryObject.*:ResourceWarning",
            )
            config.addinivalue_line(
                "filterwarnings",
                "ignore:.*Unclosed <MemoryObject.*:pytest.PytestUnraisableExceptionWarning",
            )
            config.addinivalue_line(
                "filterwarnings",
                "ignore:Exception ignored in.*<function MemoryObject.*.__del__.*:pytest.PytestUnraisableExceptionWarning",
            )
            config.addinivalue_line(
                "filterwarnings",
                "ignore:Exception ignored in.*<socket.socket.*:pytest.PytestUnraisableExceptionWarning",
            )
            config.addinivalue_line(
                "filterwarnings",
                "ignore:Exception ignored while finalizing socket <socket.socket.*:pytest.PytestUnraisableExceptionWarning",
            )
            config.addinivalue_line(
                "filterwarnings",
                "ignore:unclosed <socket.socket.*:ResourceWarning",
            )
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
        return result

    testdir.run_and_assert = run_and_assert

    def make_graphql_schema_file(schema: str, extension=".gql"):
        return testdir.makefile(extension, schema=schema)

    testdir.make_graphql_schema_file = make_graphql_schema_file

    return testdir


@pytest.fixture
def response_factory():
    def httpx_factory(
        *,
        content: bytes = b"{}",
        content_type: str | None = "application/json",
        status_code: int = 200,
        headers: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = headers or {}
        if content_type:
            headers.setdefault("Content-Type", content_type)
        response = httpx.Response(
            status_code=status_code,
            headers=headers,
            content=content,
            request=httpx.Request(method="POST", url="http://127.0.0.1", headers=headers),
        )
        response.elapsed = datetime.timedelta(seconds=1)
        return response

    def requests_factory(
        *,
        content: bytes = b"{}",
        content_type: str | None = "application/json",
        status_code: int = 200,
        headers: dict[str, Any] | None = None,
    ) -> requests.Response:
        response = requests.Response()
        response._content = content
        response.status_code = status_code
        headers = headers or {}
        if content_type:
            headers.setdefault("Content-Type", content_type)
        headers.setdefault("Content-Length", str(len(content)))
        response.headers.update(headers)
        response.raw = HTTPResponse(body=io.BytesIO(content), status=status_code, headers=response.headers)
        response.request = requests.PreparedRequest()
        response.request.prepare(method="POST", url="http://127.0.0.1", headers=headers)
        return response

    def werkzeug_factory(
        *,
        content: bytes = b"{}",
        content_type: str | None = "application/json",
        status_code: int = 200,
        headers: dict[str, Any] | None = None,
    ):
        headers = headers or {}
        if content_type:
            headers.setdefault("Content-Type", content_type)
        request = Request.from_values(method="POST", base_url="http://127.0.0.1", path="/test", headers=headers)
        response = TestResponse(
            response=iter([content]),
            status=str(status_code),
            headers=Headers(headers),
            request=request,
        )
        return response

    return SimpleNamespace(httpx=httpx_factory, requests=requests_factory, wsgi=werkzeug_factory)


@pytest.fixture
def case_factory(swagger_20):
    def factory(**kwargs):
        operation = kwargs.pop("operation", swagger_20["/users"]["get"])
        kwargs.setdefault("method", "GET")
        kwargs.setdefault("media_type", "application/json")
        return operation.Case(**kwargs)

    return factory


@dataclass
class CurlWrapper:
    testdir: field()

    def run(self, command: str):
        # Strip warning messages that may be appended to the command
        if "⚠️" in command:
            command = command.split("⚠️")[0].strip()
        return self.testdir.run(*shlex.split(command))

    def assert_valid(self, command: str):
        result = self.run(command)
        if result.ret != 0:
            # The command is valid, but the target is not reachable
            assert "Failed to connect" in result.stderr.lines[-1]


@pytest.fixture
def curl(testdir):
    return CurlWrapper(testdir)


RESPONSE = Response(
    status_code=200,
    headers={},
    content=b"",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)


@pytest.fixture
def mocked_call(mocker):
    mocker.patch("schemathesis.Case.call", return_value=RESPONSE)
