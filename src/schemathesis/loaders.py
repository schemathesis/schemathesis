# pylint: disable=too-many-arguments
import os
from typing import IO, Any, Callable, Dict, Optional, Union
from urllib.parse import urljoin

import requests
import yaml
from werkzeug.test import Client

from .constants import USER_AGENT
from .exceptions import HTTPError
from .lazy import LazySchema
from .schemas import BaseSchema, OpenApi30, SwaggerV20
from .types import Filter, PathLike
from .utils import NOT_SET, StringDatesYAMLLoader, WSGIResponse, deprecated, get_base_url


def from_path(
    path: PathLike,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    *,
    app: Any = None,
) -> BaseSchema:
    """Load a file from OS path and parse to schema instance."""
    with open(path) as fd:
        return from_file(
            fd, location=os.path.abspath(path), base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app
        )


def from_uri(
    uri: str,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    *,
    app: Any = None,
    **kwargs: Any,
) -> BaseSchema:
    """Load a remote resource and parse to schema instance."""
    kwargs.setdefault("headers", {}).setdefault("User-Agent", USER_AGENT)
    response = requests.get(uri, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise HTTPError(response=response, url=uri)
    if base_url is None:
        base_url = get_base_url(uri)
    return from_file(response.text, location=uri, base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app)


def from_file(
    file: Union[IO[str], str],
    location: Optional[str] = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    *,
    app: Any = None,
) -> BaseSchema:
    """Load a file content and parse to schema instance.

    `file` could be a file descriptor, string or bytes.
    """
    raw = yaml.load(file, StringDatesYAMLLoader)
    return from_dict(raw, location=location, base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app)


def from_dict(
    raw_schema: Dict[str, Any],
    location: Optional[str] = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    *,
    app: Any = None,
) -> BaseSchema:
    """Get a proper abstraction for the given raw schema."""
    if "swagger" in raw_schema:
        return SwaggerV20(
            raw_schema, location=location, base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app
        )

    if "openapi" in raw_schema:
        return OpenApi30(
            raw_schema, location=location, base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app
        )
    raise ValueError("Unsupported schema type")


def from_pytest_fixture(
    fixture_name: str,
    method: Optional[Filter] = NOT_SET,
    endpoint: Optional[Filter] = NOT_SET,
    tag: Optional[Filter] = NOT_SET,
) -> LazySchema:
    """Needed for a consistent library API."""
    return LazySchema(fixture_name, method=method, endpoint=endpoint, tag=tag)


def from_wsgi(
    schema_path: str,
    app: Any,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
) -> BaseSchema:
    client = Client(app, WSGIResponse)
    response = client.get(schema_path, headers={"User-Agent": USER_AGENT})  # type: ignore
    # Raising exception to provide unified behavior
    # E.g. it will be handled in CLI - a proper error message will be shown
    if 400 <= response.status_code < 600:
        raise HTTPError(response=response, url=schema_path)
    return from_file(
        response.data, location=schema_path, base_url=base_url, method=method, endpoint=endpoint, tag=tag, app=app
    )


def get_loader_for_app(app: Any) -> Callable:
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
) -> BaseSchema:
    from .extra._aiohttp import run_server  # pylint: disable=import-outside-toplevel

    port = run_server(app)
    app_url = f"http://127.0.0.1:{port}/"
    url = urljoin(app_url, schema_path)
    if not base_url:
        base_url = app_url
    return from_uri(url, base_url=base_url, method=method, endpoint=endpoint, tag=tag)


# Backward compatibility
class Parametrizer:
    from_path = deprecated(from_path, "`Parametrizer.from_path` is deprecated, use `schemathesis.from_path` instead.")
    from_uri = deprecated(from_uri, "`Parametrizer.from_uri` is deprecated, use `schemathesis.from_uri` instead.")
