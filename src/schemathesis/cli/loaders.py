"""Automatic schema loading.

This module handles the automatic detection and loading of API schemas,
supporting both GraphQL and OpenAPI specifications.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Iterable
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

import click

from schemathesis import graphql, openapi
from schemathesis.cli.constants import MISSING_BASE_URL_MESSAGE
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.config import ProjectConfig
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.fs import file_exists
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted

if TYPE_CHECKING:
    from schemathesis.engine import events
    from schemathesis.schemas import BaseSchema

Loader = Callable[["ProjectConfig"], "BaseSchema"]


def into_event_stream(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    engine_callback: Callable[[BaseSchema], Iterable[events.EngineEvent]],
) -> EventGenerator:
    # The whole engine idea is that it communicates with the outside via events, so handlers can react to them.
    # For this reason, even schema loading is done via a separate set of events.
    loading_started = LoadingStarted(location=location)
    yield loading_started

    try:
        schema = load_schema(location=location, config=config)
        # Schemas don't (yet?) use configs for deciding what operations should be tested, so
        # a separate FilterSet is passed there. It combines both config file filters + CLI options.
        schema.filter_set = schema.config.operations.create_filter_set(**filter_set)
        if file_exists(location) and schema.config.base_url is None:
            raise click.UsageError(MISSING_BASE_URL_MESSAGE)
    except KeyboardInterrupt:
        yield Interrupted(phase=None)
        return
    except LoaderError as exc:
        yield FatalError(exception=exc)
        return

    yield LoadingFinished(
        location=location,
        start_time=loading_started.timestamp,
        base_url=schema.get_base_url(),
        specification=schema.specification,
        statistic=schema.statistic,
        schema=schema.raw_schema,
        config=schema.config,
        base_path=schema.base_path,
        find_operation_by_label=schema.find_operation_by_label,
    )

    try:
        yield from engine_callback(schema)
    except Exception as exc:
        yield FatalError(exception=exc)


def load_schema(location: str, config: ProjectConfig) -> BaseSchema:
    """Load API schema automatically based on the provided configuration."""
    if is_probably_graphql(location):
        # Try GraphQL first, then fallback to Open API
        return _try_load_schema(location, config, graphql, openapi)
    # Try Open API first, then fallback to GraphQL
    return _try_load_schema(location, config, openapi, graphql)


def should_try_more(exc: LoaderError) -> bool:
    """Determine if alternative schema loading should be attempted."""
    import requests
    from yaml.reader import ReaderError

    if (isinstance(exc.__cause__, ReaderError) and "characters are not allowed" in str(exc.__cause__)) or (
        isinstance(exc.__cause__, JSONDecodeError)
        and ('"swagger"' in exc.__cause__.doc or '"openapi"' in exc.__cause__.doc)
    ):
        return False

    # We should not try other loaders for cases when we can't even establish connection
    return not isinstance(exc.__cause__, requests.exceptions.ConnectionError) and exc.kind not in (
        LoaderErrorKind.OPEN_API_INVALID_SCHEMA,
        LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION,
        LoaderErrorKind.OPEN_API_UNSUPPORTED_VERSION,
    )


def detect_loader(location: str, module: Any) -> Callable:
    """Detect API schema loader."""
    if file_exists(location):
        return module.from_path
    return module.from_url


def _try_load_schema(location: str, config: ProjectConfig, first_module: Any, second_module: Any) -> BaseSchema:
    """Try to load schema with fallback option."""
    from urllib3.exceptions import InsecureRequestWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return _load_schema(location, config, first_module)
        except LoaderError as exc:
            # If this was the OpenAPI loader on an explicit OpenAPI file, don't fallback
            if first_module is openapi and is_openapi_file(location):
                raise exc
            if should_try_more(exc):
                try:
                    return _load_schema(location, config, second_module)
                except Exception as second_exc:
                    if is_specific_exception(second_exc):
                        raise second_exc
            # Re-raise the original error
            raise exc


def _load_schema(location: str, config: ProjectConfig, module: Any) -> BaseSchema:
    """Unified schema loader for both GraphQL and OpenAPI."""
    loader = detect_loader(location, module)

    kwargs: dict = {}
    if loader is module.from_url:
        if config.wait_for_schema is not None:
            kwargs["wait_for_schema"] = config.wait_for_schema
        kwargs["verify"] = config.tls_verify
        request_cert = config.request_cert_for()
        if request_cert:
            kwargs["cert"] = request_cert
        auth = config.auth_for()
        if auth is not None:
            kwargs["auth"] = auth
        headers = config.headers_for()
        if headers:
            kwargs["headers"] = headers

    return loader(location, config=config._parent, **kwargs)


def is_specific_exception(exc: Exception) -> bool:
    """Determine if alternative schema loading should be attempted."""
    return (
        isinstance(exc, LoaderError)
        and exc.kind == LoaderErrorKind.GRAPHQL_INVALID_SCHEMA
        # In some cases it is not clear that the schema is even supposed to be GraphQL, e.g. an empty input
        and "Syntax Error: Unexpected <EOF>." not in exc.extras
    )


def is_probably_graphql(location: str) -> bool:
    """Detect whether it is likely that the given location is a GraphQL endpoint."""
    return location.endswith(("/graphql", "/graphql/", ".graphql", ".gql"))


def is_openapi_file(location: str) -> bool:
    name = os.path.basename(location).lower()
    return any(name == f"{base}{ext}" for base in ("openapi", "swagger") for ext in (".json", ".yaml", ".yml"))
