from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from textwrap import dedent
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
from schemathesis.core import storage
from schemathesis.core.transport import Response

from .utils import make_schema

pytest_plugins = [
    "pytester",
    "pytest_mock",
    "test.fixtures.ctx",
    "test.fixtures.crashes",
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


def _get_current_coverage() -> Any | None:
    try:
        from coverage import Coverage
    except Exception:
        return None
    try:
        return Coverage.current()
    except Exception:
        return None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    coverage = _get_current_coverage()
    if coverage is not None:
        # Some xdist workers don't reliably persist coverage data during interpreter shutdown.
        coverage.save()


@pytest.fixture(scope="session")
def hypothesis_max_examples():
    # Returns max_examples when overridden via `--hypothesis-profile`, else None so each test can pick its own.
    value = settings().max_examples
    return None if value == 100 else value


@pytest.fixture(autouse=True)
def _isolate_schemathesis_state(tmp_path, monkeypatch):
    """Redirect the per-project artifact root into `tmp_path` so xdist workers don't leak state."""
    monkeypatch.setattr(storage, "DEFAULT_ROOT", tmp_path / ".schemathesis")


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


@dataclass(frozen=True)
class ResponseFactory:
    httpx: Callable[..., httpx.Response]
    requests: Callable[..., requests.Response]
    wsgi: Callable[..., TestResponse]


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
        response.raw = HTTPResponse(body=BytesIO(content), status=status_code, headers=response.headers)
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

    return ResponseFactory(httpx=httpx_factory, requests=requests_factory, wsgi=werkzeug_factory)


@pytest.fixture
def case_factory(swagger_20):
    def factory(**kwargs):
        operation = kwargs.pop("operation", None) or swagger_20["/users"]["get"]
        kwargs.setdefault("method", "GET")
        kwargs.setdefault("media_type", "application/json")
        return operation.Case(**kwargs)

    return factory


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
