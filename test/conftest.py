from __future__ import annotations

import datetime
import io
import logging
import os
import platform
import re
import shlex
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import click
import httpx
import pytest
import requests
import tomli_w
import yaml
from _pytest.main import ExitCode
from click.testing import CliRunner, Result
from hypothesis import settings
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode
from urllib3 import HTTPResponse
from werkzeug import Request
from werkzeug.datastructures import Headers
from werkzeug.test import TestResponse

import schemathesis.cli
from schemathesis import auths, hooks
from schemathesis.cli.commands.run.executor import CUSTOM_HANDLERS
from schemathesis.cli.commands.run.handlers import output
from schemathesis.core import deserialization
from schemathesis.core.hooks import HOOKS_MODULE_ENV_VAR
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.specs.openapi import media_types
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

from .apps import _graphql as graphql
from .apps import openapi
from .apps.openapi.schema import OpenAPIVersion, Operation
from .utils import make_schema

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from syrupy.types import PropertyFilter, PropertyMatcher

pytest_plugins = [
    "pytester",
    "aiohttp.pytest_plugin",
    "pytest_mock",
    "test.fixtures.ctx",
    "test.fixtures.app_runner",
]

logging.getLogger("pyrate_limiter").setLevel(logging.CRITICAL)

# Register Hypothesis profile. Could be used as
# `pytest test -m hypothesis --hypothesis-profile <profile-name>`
settings.register_profile("CI", max_examples=2000)

output.SCHEMATHESIS_VERSION = "dev"


@pytest.fixture(autouse=True)
def reset_hooks():
    # Store built-in deserializers to restore after test
    builtin_deserializers = deserialization.deserializers().copy()

    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    for transport in (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT):
        transport.unregister_serializer(*media_types.MEDIA_TYPES.keys())
    media_types.unregister_all()
    yield
    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    for transport in (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT):
        transport.unregister_serializer(*media_types.MEDIA_TYPES.keys())
    media_types.unregister_all()
    # Restore built-in deserializers
    current = list(deserialization.deserializers().keys())
    deserialization.unregister_deserializer(*current)
    for media_type, func in builtin_deserializers.items():
        deserialization.register_deserializer(func, media_type)


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
    config.addinivalue_line("markers", "snapshot(**kwargs): Configure snapshot tests.")
    config.addinivalue_line("markers", "snapshot_suffix(suffix): Append a suffix to the snapshot file name.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
    config.addinivalue_line(
        "markers",
        "openapi_version(*versions): Restrict test parametrization only to the specified Open API version(s).",
    )
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)


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
def server(_app, app_runner):
    """Run the app on an unused port."""
    port = app_runner.run_aiohttp_app(_app)
    return {"port": port}


@pytest.fixture
def server_host(server):
    return f"127.0.0.1:{server['port']}"


@pytest.fixture
def server_address(server_host):
    return f"http://{server_host}"


@pytest.fixture
def base_url(server_address, app):
    """Base URL for the running application."""
    return f"{server_address}/api"


@pytest.fixture
def openapi2_base_url(server_address, openapi_2_app):
    return f"{server_address}/api"


@pytest.fixture
def openapi3_base_url(server_address, openapi_3_app):
    return f"{server_address}/api"


@pytest.fixture
def schema_url(server_address, app):
    """URL of the schema of the running application."""
    return f"{server_address}/schema.yaml"


@pytest.fixture
def openapi2_schema_url(server_address, openapi_2_app):
    """URL of the schema of the running application."""
    return f"{server_address}/schema.yaml"


@pytest.fixture
def openapi3_schema_url(server_address, openapi_3_app):
    """URL of the schema of the running application."""
    return f"{server_address}/schema.yaml"


@pytest.fixture
def openapi3_schema(openapi3_schema_url):
    return schemathesis.openapi.from_url(openapi3_schema_url)


@pytest.fixture
def graphql_path():
    return "/graphql"


@pytest.fixture
def graphql_app(graphql_path):
    return graphql._flask.create_app(graphql_path)


@pytest.fixture
def graphql_server(graphql_app, app_runner):
    port = app_runner.run_flask_app(graphql_app)
    return {"port": port}


@pytest.fixture
def graphql_server_host(graphql_server):
    return f"127.0.0.1:{graphql_server['port']}"


@pytest.fixture
def graphql_url(graphql_server_host, graphql_path):
    return f"http://{graphql_server_host}{graphql_path}"


@pytest.fixture
def graphql_schema(graphql_url):
    return schemathesis.graphql.from_url(graphql_url)


@pytest.fixture
def graphql_strategy(graphql_schema):
    return graphql_schema["Query"]["getBooks"].as_strategy()


@contextmanager
def keep_cwd():
    cwd = os.getcwd()
    try:
        yield
    finally:
        os.chdir(cwd)


FLASK_MARKERS = ("* Serving Flask app", "* Debug mode")
PACKAGE_ROOT = Path(schemathesis.__file__).parent
SITE_PACKAGES = requests.__file__.split("requests")[0]
IS_WINDOWS = platform.system() == "Windows"


@dataclass
class CliSnapshotConfig:
    request: FixtureRequest
    replace_server_host: bool = True
    replace_tmp_dir: bool = True
    replace_duration: bool = True
    replace_error_codes: bool = True
    replace_test_case_id: bool = True
    replace_uuid: bool = True
    replace_response_time: bool = True
    replace_seed: bool = True
    replace_reproduce_with: bool = False
    replace_test_cases: bool = True
    replace_phase_statistic: bool = False
    replace_stateful_statistic: bool = True
    remove_last_line: bool = False
    replace: bool = True

    @classmethod
    def from_request(cls, request: FixtureRequest) -> CliSnapshotConfig:
        marker = request.node.get_closest_marker("snapshot")
        if marker is not None:
            return cls(request, **marker.kwargs)
        return cls(request)

    @property
    def testdir(self):
        return self.request.getfixturevalue("testdir")

    def serialize(self, data: str) -> str:
        if not self.replace:
            return data
        if self.replace_test_cases:
            data = re.sub(r"Test cases:\n  (\d+) generated, \1 skipped", "Test cases:\n  N generated, N skipped", data)
            # Cases with failures and skips
            data = re.sub(
                r"Test cases:\n  (\d+) generated, (\d+) found (\d+) unique failures, (\d+) skipped",
                "Test cases:\n  N generated, N found N unique failures, N skipped",
                data,
            )
            # Cases with passed and skips
            data = re.sub(
                r"Test cases:\n  (\d+) generated, (\d+) passed, (\d+) skipped",
                "Test cases:\n  N generated, N passed, N skipped",
                data,
            )
            # Only passed cases
            data = re.sub(r"Test cases:\n  (\d+) generated, (\d+) passed", "Test cases:\n  N generated, N passed", data)
            # Cases with failures but no skips
            data = re.sub(
                r"Test cases:\n  (\d+) generated, (\d+) found (\d+) unique failures",
                "Test cases:\n  N generated, N found N unique failures",
                data,
            )
        if self.replace_server_host:
            used_fixtures = self.request.fixturenames
            for fixture in ("graphql_server_host", "server_host"):
                if fixture in used_fixtures:
                    try:
                        host = self.request.getfixturevalue(fixture)
                        data = data.replace(host, "127.0.0.1")
                    except LookupError:
                        pass
            with keep_cwd():
                data = data.replace(Path(self.testdir.tmpdir).as_uri(), "file:///tmp")
        data = re.sub(r"http://127\.0\.0\.1:[0-9]{3,}", "http://127.0.0.1", data)
        if self.replace_tmp_dir:
            with keep_cwd():
                data = data.replace(str(self.testdir.tmpdir) + os.path.sep, "/tmp/")
                data = data.replace(str(Path(self.testdir.tmpdir).parent) + os.path.sep, "/tmp/")
        if "Configuration:" in data:
            lines = []
            for line in data.splitlines():
                normalized = click.unstyle(line)
                stripped = normalized.lstrip()
                if stripped.startswith("Configuration:"):
                    indent = " " * (len(normalized) - len(stripped))
                    lines.append(f"{indent}Configuration:    /tmp/config.toml")
                else:
                    lines.append(line)
            data = "\n".join(lines)
        package_root = "/package-root"
        site_packages = "/site-packages/"
        data = data.replace(str(PACKAGE_ROOT), package_root)
        data = re.sub(
            "❌  Failed to load configuration file from .*toml$",
            "❌  Failed to load configuration file from config.toml",
            data,
            flags=re.MULTILINE,
        )
        version_line = "Schemathesis dev"
        data = data.replace(f"Schemathesis v{SCHEMATHESIS_VERSION}", version_line)
        data = re.sub("━+", "━" * len(version_line), data)
        data = data.replace(str(SITE_PACKAGES), site_packages)
        data = re.sub(", line [0-9]+,", ", line XXX,", data)
        data = re.sub(r"Scenarios:.*\d+", r"Scenarios:    N", data)
        if self.replace_phase_statistic:
            data = re.sub("🚫 [0-9]+ errors", "🚫 1 error", data)
        if "Stateful" in data:
            if self.replace_stateful_statistic:
                data = re.sub(r"API Links:.*\d+ covered", r"API Links:    N covered", data)
            before, after = data.split("Stateful", 1)
            after = re.sub(r"\d+ passed", "N passed", after)
            data = before + "Stateful" + after

        if "Traceback (most recent call last):" in data:
            lines = [line for line in data.splitlines() if set(line) not in ({" ", "^"}, {" ", "^", "~"})]
            comprehension_ids = [idx for idx, line in enumerate(lines) if line.strip().endswith("comp>")]
            # Drop frames that are related to comprehensions
            for idx in comprehension_ids[::-1]:
                lines.pop(idx)
                lines.pop(idx)
            if platform.system() == "Windows":
                for idx, line in enumerate(lines):
                    if line.strip().startswith("File") and "line" in line:
                        lines[idx] = line.replace("\\", "/")
            data = "\n".join(lines)
        if self.replace_error_codes:
            data = (
                data.replace("Errno 111", "Error NUM")
                .replace("Errno 61", "Error NUM")
                .replace("WinError 10061", "Error NUM")
                .replace("Cannot connect to proxy.", "Unable to connect to proxy")
            )
            data = data.replace(
                "No connection could be made because the target machine actively refused it", "Connection refused"
            )
        if self.replace_duration:
            data = re.sub(r"It took [0-9]+\.[0-9]{2}s", "It took 0.50s", data)
            data = re.sub(r"\(in [0-9]+\.[0-9]{2}s\)", "(in 0.00s)", data)
            data = re.sub(r"after [0-9]+\.[0-9]{2}s", "after 0.00s", data).strip()
            lines = data.splitlines()
            lines[-1] = re.sub(r"in [0-9]+\.[0-9]{2}s", "in 1.00s", lines[-1])
            if "in 1.00s" in lines[-1]:
                lines[-1] = lines[-1].strip("=").center(80, "=")
            data = "\n".join(lines) + "\n"
        if self.remove_last_line:
            lines = data.splitlines()
            data = "\n".join(lines[:-1])
        if self.replace_test_case_id:
            lines = data.splitlines()
            for idx, line in enumerate(lines):
                if re.match(r".*\d+\. Test Case ID", line):
                    sequential_id = lines[idx].split(".")[0]
                    lines[idx] = f"{sequential_id}. Test Case ID: <PLACEHOLDER>"
            data = "\n".join(lines) + "\n"
        if self.replace_uuid:
            data = re.sub(r"\b[0-9a-fA-F]{32}\b", EXAMPLE_UUID, data)
        if self.replace_response_time:
            data = re.sub(r"Actual: \d+\.\d+ms", "Actual: 105.00ms", data)
        if self.replace_seed:
            data = re.sub(r"--seed=\d+", "--seed=42", data)
            data = re.sub(r"Seed: \d+", "Seed: 42", data)
        if self.replace_reproduce_with:
            lines = []
            seen = False
            for line in data.splitlines():
                if "curl" in line:
                    if not seen:
                        lines.append("    <PLACEHOLDER>")
                        seen = True
                else:
                    seen = False
                    lines.append(line)
            data = "\n".join(lines) + "\n"
        lines = []
        for line in data.splitlines():
            line = click.unstyle(line)
            if line.endswith("Schema Loading Error"):
                # It is written at the end of the current line and does not properly rewrite the current line
                # on all terminals
                lines.append("Schema Loading Error")
                continue
            if IS_WINDOWS and ("Loading specification" in line or "Loaded specification" in line):
                line = line.replace("\\", "/")
            if any(marker in line for marker in FLASK_MARKERS) or line.lstrip().startswith(
                (
                    "🕛 ",
                    "🕐 ",
                    "🕑 ",
                    "🕒 ",
                    "🕓 ",
                    "🕔 ",
                    "🕕 ",
                    "🕖 ",
                    "🕗 ",
                    "🕘 ",
                    "🕙 ",
                    "🕚 ",
                    "⠋",
                    "⠙",
                    "⠹",
                    "⠸",
                    "⠼",
                    "⠴",
                    "⠦",
                    "⠧",
                    "⠇",
                    "⠏",
                    "0:0",
                )
            ):
                continue
            lines.append(line.rstrip())
        lines = clean_unit_tests(lines)
        lines = clean_stateful_tests(lines)
        return "\n".join(lines).strip() + "\n"


def clean_unit_tests(lines):
    for idx, line in enumerate(lines):
        if "API capabilities" in line:
            probing_idx = idx + 4
            break
        if "API probing" in line:
            probing_idx = idx + 2
            break
    else:
        return lines

    indices = []
    for idx, line in enumerate(lines[probing_idx:], start=probing_idx):
        if any(f"{phase} (in" in line for phase in ("Examples", "Coverage", "Fuzzing")):
            indices.append(idx)

    if not indices:
        return lines

    output = lines[:probing_idx]
    for idx in indices[:-1]:
        output += lines[idx : idx + 4]
    output += lines[indices[-1] :]
    return output


def clean_stateful_tests(lines):
    start_idx = None
    for i, line in enumerate(lines):
        if "Fuzzing (in" in line:
            start_idx = i + 3
            break
    if start_idx is None:
        for i, line in enumerate(lines):
            if "API probing failed" in line:
                start_idx = i + 1
                break
            if "API capabilities" in line:
                start_idx = i + 3
                break

    end_idx = None
    for i, line in enumerate(lines):
        if "Stateful (in" in line:
            end_idx = i
            break

    if start_idx is not None and end_idx is not None:
        return lines[: start_idx + 1] + lines[end_idx:]
    return lines


EXAMPLE_UUID = "e32ab85ed4634c38a320eb0b22460da9"


@pytest.fixture
def snapshot_cli(request, snapshot):
    config = CliSnapshotConfig.from_request(request)
    snapshot_suffix = request.node.get_closest_marker("snapshot_suffix")

    class CliSnapshotExtension(SingleFileSnapshotExtension):
        _write_mode = WriteMode.TEXT

        def serialize(
            self,
            data: Result | pytest.RunResult,
            *,
            exclude: PropertyFilter | None = None,
            include: PropertyFilter | None = None,
            matcher: PropertyMatcher | None = None,
        ) -> str:
            stdout = ""
            if isinstance(data, Result):
                exit_code = data.exit_code
                if data.stdout_bytes:
                    stdout = data.stdout
                if data.stderr_bytes:
                    stdout += data.stderr
            else:
                exit_code = data.ret
                stdout = data.stdout.str() + data.stderr.str()
            serialized = f"Exit code: {exit_code}"
            if stdout:
                serialized += f"\n---\nStdout:\n{stdout}"
            return config.serialize(serialized).replace("\r\n", "\n").replace("\r", "\n")

        @classmethod
        def get_snapshot_name(cls, *, test_location, index=0) -> str:
            base_name = super().get_snapshot_name(test_location=test_location, index=index)
            if snapshot_suffix is not None:
                suffix = str(snapshot_suffix.args[0])
                return f"{base_name}.{suffix}"
            return base_name

    class SnapshotAssertion(snapshot.__class__):
        def rebuild(self):
            return self.use_extension(extension_class=CliSnapshotExtension)

    snapshot.__class__ = SnapshotAssertion
    return snapshot.rebuild()


@pytest.fixture
def cli(tmp_path):
    """CLI runner helper.

    Provides in-process execution via `click.CliRunner`.
    """
    cli_runner = CliRunner()

    class Runner:
        @staticmethod
        def run(*args, **kwargs):
            return Runner.main("run", *args, **kwargs)

        @staticmethod
        def main(*args, config=None, hooks=None, **kwargs):
            if config is not None:
                path = tmp_path / "config.toml"
                path.write_text(tomli_w.dumps(config), encoding="utf-8")
                args = ["--config-file", str(path), *args]
            if hooks is not None:
                env = kwargs.setdefault("env", {})
                env[HOOKS_MODULE_ENV_VAR] = hooks
            result = cli_runner.invoke(schemathesis.cli.schemathesis, args, **kwargs)
            if result.exception and not isinstance(result.exception, SystemExit):
                raise result.exception
            return result

        @staticmethod
        def run_and_assert(*args, exit_code: ExitCode = ExitCode.OK, **kwargs):
            result = Runner.run(*args, **kwargs)
            assert result.exit_code == exit_code, result.stdout
            return result

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
    return ctx.openapi.build_schema(
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

    return ctx.openapi.build_schema(
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
def swagger_20(simple_schema):
    return schemathesis.openapi.from_dict(simple_schema)


@pytest.fixture
def openapi_30():
    raw = make_schema("simple_openapi.yaml")
    return schemathesis.openapi.from_dict(raw)


@pytest.fixture
def openapi_31():
    raw = make_schema("simple_openapi.yaml")
    raw["openapi"] = "3.1.0"
    return schemathesis.openapi.from_dict(raw)


@pytest.fixture
def app_schema(openapi_version, operations):
    return openapi._aiohttp.make_openapi_schema(operations=operations, version=openapi_version)


@pytest.fixture
def testdir(testdir):
    def maker(
        content,
        pytest_plugins=("aiohttp.pytest_plugin",),
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
def wsgi_app_factory():
    return openapi._flask.create_app


@pytest.fixture
def flask_app(wsgi_app_factory, operations):
    return wsgi_app_factory(operations)


@pytest.fixture
def asgi_app_factory():
    return openapi._fastapi.create_app


@pytest.fixture
def fastapi_app(asgi_app_factory):
    return asgi_app_factory()


@pytest.fixture
def fastapi_graphql_app(graphql_path):
    return graphql._fastapi.create_app(graphql_path)


@pytest.fixture
def real_app_schema(schema_url):
    return schemathesis.openapi.from_url(schema_url)


@pytest.fixture
def wsgi_app_schema(flask_app):
    return schemathesis.openapi.from_wsgi("/schema.yaml", flask_app)


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
