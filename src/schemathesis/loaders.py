import pathlib
from typing import IO, Any, Callable, Dict, Iterable, Optional, Union
from urllib.parse import urljoin

import jsonschema
import requests
import yaml
from jsonschema import ValidationError
from starlette.applications import Starlette
from starlette.testclient import TestClient as ASGIClient
from werkzeug.test import Client
from yarl import URL

from .constants import DEFAULT_DATA_GENERATION_METHODS, USER_AGENT, DataGenerationMethod
from .exceptions import HTTPError
from .hooks import HookContext, dispatch
from .lazy import LazySchema
from .specs.openapi import definitions
from .specs.openapi.schemas import BaseOpenAPISchema, OpenApi30, SwaggerV20
from .types import Filter, PathLike
from .utils import NOT_SET, StringDatesYAMLLoader, WSGIResponse


def from_path(
    path: PathLike,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    *,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
) -> BaseOpenAPISchema:
    """Load a file from OS path and parse to schema instance."""
    with open(path) as fd:
        return from_file(
            fd,
            location=pathlib.Path(path).absolute().as_uri(),
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=app,
            validate_schema=validate_schema,
            skip_deprecated_endpoints=skip_deprecated_endpoints,
            data_generation_methods=data_generation_methods,
            force_schema_version=force_schema_version,
        )


def from_uri(
    uri: str,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    port: Optional[int] = None,
    *,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load a remote resource and parse to schema instance."""
    _setup_headers(kwargs)
    if not base_url and port:
        base_url = str(URL(uri).with_port(port))
    response = requests.get(uri, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise HTTPError(response=response, url=uri) from exc
    return from_file(
        response.text,
        location=uri,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        app=app,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
    )


def from_file(
    file: Union[IO[str], str],
    location: Optional[str] = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    *,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    **kwargs: Any,  # needed in the runner to have compatible API across all loaders
) -> BaseOpenAPISchema:
    """Load a file content and parse to schema instance.

    `file` could be a file descriptor, string or bytes.
    """
    raw = yaml.load(file, StringDatesYAMLLoader)
    return from_dict(
        raw,
        location=location,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        app=app,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
    )


def from_dict(
    raw_schema: Dict[str, Any],
    location: Optional[str] = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    *,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
) -> BaseOpenAPISchema:
    """Get a proper abstraction for the given raw schema."""
    dispatch("before_load_schema", HookContext(), raw_schema)

    def init_openapi_2() -> SwaggerV20:
        _maybe_validate_schema(raw_schema, definitions.SWAGGER_20, validate_schema)
        return SwaggerV20(
            raw_schema,
            location=location,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=app,
            validate_schema=validate_schema,
            skip_deprecated_endpoints=skip_deprecated_endpoints,
            data_generation_methods=data_generation_methods,
        )

    def init_openapi_3() -> OpenApi30:
        _maybe_validate_schema(raw_schema, definitions.OPENAPI_30, validate_schema)
        return OpenApi30(
            raw_schema,
            location=location,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=app,
            validate_schema=validate_schema,
            skip_deprecated_endpoints=skip_deprecated_endpoints,
            data_generation_methods=data_generation_methods,
        )

    if force_schema_version == "20":
        return init_openapi_2()
    if force_schema_version == "30":
        return init_openapi_3()
    if "swagger" in raw_schema:
        return init_openapi_2()
    if "openapi" in raw_schema:
        return init_openapi_3()
    raise ValueError("Unsupported schema type")


def _maybe_validate_schema(instance: Dict[str, Any], schema: Dict[str, Any], validate_schema: bool) -> None:
    if validate_schema:
        try:
            jsonschema.validate(instance, schema)
        except TypeError as exc:
            raise ValidationError("Invalid schema") from exc


def from_pytest_fixture(
    fixture_name: str,
    method: Optional[Filter] = NOT_SET,
    endpoint: Optional[Filter] = NOT_SET,
    tag: Optional[Filter] = NOT_SET,
    operation_id: Optional[Filter] = NOT_SET,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
) -> LazySchema:
    """Needed for a consistent library API."""
    return LazySchema(
        fixture_name,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
    )


def from_wsgi(
    schema_path: str,
    app: Any,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    _setup_headers(kwargs)
    client = Client(app, WSGIResponse)
    response = client.get(schema_path, **kwargs)
    # Raising exception to provide unified behavior
    # E.g. it will be handled in CLI - a proper error message will be shown
    if 400 <= response.status_code < 600:
        raise HTTPError(response=response, url=schema_path)
    return from_file(
        response.data,
        location=schema_path,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        app=app,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
    )


def get_loader_for_app(app: Any) -> Callable:
    if isinstance(app, Starlette):
        return from_asgi
    if app.__class__.__module__.startswith("aiohttp."):
        return from_aiohttp
    return from_wsgi


def from_aiohttp(
    schema_path: str,
    app: Any,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    *,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    from .extra._aiohttp import run_server  # pylint: disable=import-outside-toplevel

    port = run_server(app)
    app_url = f"http://127.0.0.1:{port}/"
    url = urljoin(app_url, schema_path)
    return from_uri(
        url,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
        **kwargs,
    )


def from_asgi(
    schema_path: str,
    app: Any,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    validate_schema: bool = True,
    skip_deprecated_endpoints: bool = False,
    data_generation_methods: Iterable[DataGenerationMethod] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    _setup_headers(kwargs)
    client = ASGIClient(app)
    response = client.get(schema_path, **kwargs)
    # Raising exception to provide unified behavior
    # E.g. it will be handled in CLI - a proper error message will be shown
    if 400 <= response.status_code < 600:
        raise HTTPError(response=response, url=schema_path)
    return from_file(
        response.text,
        location=schema_path,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        app=app,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
    )


def _setup_headers(kwargs: Dict[str, Any]) -> None:
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT
