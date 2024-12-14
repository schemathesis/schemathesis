from __future__ import annotations

import enum
import json
import re
from os import PathLike
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Mapping

from schemathesis.core import media_types
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.loaders import load_from_url, prepare_request_kwargs, raise_for_status, require_relative_url
from schemathesis.hooks import HookContext, dispatch
from schemathesis.python import asgi, wsgi

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


def from_asgi(path: str, app: Any, **kwargs: Any) -> BaseOpenAPISchema:
    require_relative_url(path)
    client = asgi.get_client(app)
    response = load_from_url(client.get, url=path, **kwargs)
    content_type = detect_content_type(headers=response.headers, path=path)
    schema = load_content(response.text, content_type)
    return from_dict(schema=schema).configure(app=app, location=path)


def from_wsgi(path: str, app: Any, **kwargs: Any) -> BaseOpenAPISchema:
    require_relative_url(path)
    prepare_request_kwargs(kwargs)
    client = wsgi.get_client(app)
    response = client.get(path=path, **kwargs)
    raise_for_status(response)
    content_type = detect_content_type(headers=response.headers, path=path)
    schema = load_content(response.text, content_type)
    return from_dict(schema=schema).configure(app=app, location=path)


def from_url(url: str, *, wait_for_schema: float | None = None, **kwargs: Any) -> BaseOpenAPISchema:
    """Load from URL."""
    import requests

    response = load_from_url(requests.get, url=url, wait_for_schema=wait_for_schema, **kwargs)
    content_type = detect_content_type(headers=response.headers, path=url)
    schema = load_content(response.text, content_type)
    return from_dict(schema=schema).configure(location=url)


def from_path(path: PathLike | str, *, encoding: str = "utf-8") -> BaseOpenAPISchema:
    """Load from a filesystem path."""
    with open(path, encoding=encoding) as file:
        content_type = detect_content_type(headers=None, path=str(path))
        schema = load_content(file.read(), content_type)
        return from_dict(schema=schema).configure(location=Path(path).absolute().as_uri())


def from_file(file: IO[str] | str) -> BaseOpenAPISchema:
    """Load from file-like object or string."""
    if isinstance(file, str):
        data = file
    else:
        data = file.read()
    try:
        schema = json.loads(data)
    except json.JSONDecodeError:
        schema = _load_yaml(data)
    return from_dict(schema)


def from_dict(schema: dict[str, Any]) -> BaseOpenAPISchema:
    """Base loader that others build upon."""
    from schemathesis.specs.openapi.schemas import OpenApi30, SwaggerV20

    if not isinstance(schema, dict):
        raise LoaderError(LoaderErrorKind.OPEN_API_INVALID_SCHEMA, SCHEMA_INVALID_ERROR)
    hook_context = HookContext()
    dispatch("before_load_schema", hook_context, schema)

    if "swagger" in schema:
        instance = SwaggerV20(schema)
    elif "openapi" in schema:
        version = schema["openapi"]
        if not OPENAPI_VERSION_RE.match(version):
            raise LoaderError(
                LoaderErrorKind.OPEN_API_UNSUPPORTED_VERSION,
                f"The provided schema uses Open API {version}, which is currently not supported.",
            )
        instance = OpenApi30(schema)
    else:
        raise LoaderError(
            LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION,
            "Unable to determine the Open API version as it's not specified in the document.",
        )
    dispatch("after_load_schema", hook_context, instance)
    return instance


class ContentType(enum.Enum):
    """Known content types for schema files."""

    JSON = enum.auto()
    YAML = enum.auto()
    UNKNOWN = enum.auto()


def detect_content_type(*, headers: Mapping[str, str] | None = None, path: str | None = None) -> ContentType:
    """Detect content type from various sources."""
    if headers is not None and (content_type := _detect_from_headers(headers)) != ContentType.UNKNOWN:
        return content_type
    if path is not None and (content_type := _detect_from_path(path)) != ContentType.UNKNOWN:
        return content_type
    return ContentType.UNKNOWN


def _detect_from_headers(headers: Mapping[str, str]) -> ContentType:
    """Detect content type from HTTP headers."""
    content_type = headers.get("Content-Type", "").lower()
    try:
        if content_type and media_types.is_json(content_type):
            return ContentType.JSON
        if content_type and media_types.is_yaml(content_type):
            return ContentType.YAML
    except ValueError:
        pass
    return ContentType.UNKNOWN


def _detect_from_path(path: str) -> ContentType:
    """Detect content type from file path."""
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return ContentType.JSON
    if suffix in (".yaml", ".yml"):
        return ContentType.YAML
    return ContentType.UNKNOWN


def load_content(content: str, content_type: ContentType) -> dict[str, Any]:
    """Load content using appropriate parser."""
    if content_type == ContentType.JSON:
        return _load_json(content)
    if content_type == ContentType.YAML:
        return _load_yaml(content)
    # If type is unknown, try JSON first, then YAML
    try:
        return _load_json(content)
    except json.JSONDecodeError:
        return _load_yaml(content)


def _load_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LoaderError(
            LoaderErrorKind.SYNTAX_ERROR,
            SCHEMA_SYNTAX_ERROR,
            extras=[entry for entry in str(exc).splitlines() if entry],
        ) from exc


def _load_yaml(content: str) -> dict[str, Any]:
    import yaml

    try:
        return deserialize_yaml(content)
    except yaml.YAMLError as exc:
        kind = LoaderErrorKind.SYNTAX_ERROR
        message = SCHEMA_SYNTAX_ERROR
        extras = [entry for entry in str(exc).splitlines() if entry]
        raise LoaderError(kind, message, extras=extras) from exc


SCHEMA_INVALID_ERROR = "The provided API schema does not appear to be a valid OpenAPI schema"
SCHEMA_SYNTAX_ERROR = "API schema does not appear syntactically valid"
OPENAPI_VERSION_RE = re.compile(r"^3\.[01]\.[0-9](-.+)?$")
