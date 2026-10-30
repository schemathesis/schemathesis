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

from schemathesis import graphql, grpc, openapi
from schemathesis.cli.constants import MISSING_BASE_URL_MESSAGE
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.config import ProjectConfig, ReportFormat
from schemathesis.core.errors import HookExecutionError, IncorrectUsage, InvalidSchema, LoaderError, LoaderErrorKind
from schemathesis.core.fs import file_exists
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted
from schemathesis.grpc.loaders import BUILD_HINT

if TYPE_CHECKING:
    from types import ModuleType

    from schemathesis.engine import events
    from schemathesis.schemas import BaseSchema

GRPC_CASSETTES_MESSAGE = "HAR and VCR reports record HTTP/1.1 exchanges and are not available for gRPC targets"
GRPC_DESCRIPTOR_SET_EXTENSIONS = (".binpb", ".pb", ".protoset")
PROTO_FILE_MESSAGE = f"Schemathesis reads descriptor sets, not `.proto` files. {BUILD_HINT} and run `schemathesis run api.binpb --url URL`"


def into_event_stream(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    engine_callback: Callable[[BaseSchema], Iterable[events.EngineEvent]],
) -> EventGenerator:
    # Checked before any report handler creates its file.
    if is_grpc_location(location) and any(
        config.reports.get(report_format).enabled for report_format in (ReportFormat.HAR, ReportFormat.VCR)
    ):
        raise click.UsageError(GRPC_CASSETTES_MESSAGE)
    if location.endswith(".proto") and file_exists(location):
        raise click.UsageError(PROTO_FILE_MESSAGE)
    return _event_stream(location=location, config=config, filter_set=filter_set, engine_callback=engine_callback)


def _event_stream(
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
        # An explicitly configured base URL carries its own path, so the origin only applies without one.
        if schema.config.origin is not None and schema.config.base_url is None:
            schema.config.base_url = schema.config.origin.rstrip("/") + schema.base_path.rstrip("/")
        # Schemas don't (yet?) use configs for deciding what operations should be tested, so
        # a separate FilterSet is passed there. It combines both config file filters + CLI options.
        schema.filter_set = schema.config.operations.create_filter_set(**filter_set)
        if file_exists(location) and schema.config.base_url is None:
            message = MISSING_BASE_URL_MESSAGE
            declared = schema.declared_base_url
            if declared is not None:
                message += (
                    f"\nYour schema declares a server at {declared} - pass `--url {declared}` if that is your target."
                )
            raise click.UsageError(message)
        base_url = schema.get_base_url()
    except IncorrectUsage as exc:
        raise click.UsageError(str(exc)) from None
    except ImportError as exc:
        # A loader that needs an optional extra names it in the message.
        raise click.ClickException(str(exc)) from None
    except KeyboardInterrupt:
        yield Interrupted(phase=None)
        return
    except (LoaderError, HookExecutionError) as exc:
        yield FatalError(exception=exc)
        return
    except InvalidSchema as exc:
        yield FatalError(exception=LoaderError(LoaderErrorKind.OPEN_API_INVALID_SCHEMA, exc.message))
        return

    yield LoadingFinished.from_schema(
        schema, location=location, start_time=loading_started.timestamp, base_url=base_url
    )

    try:
        yield from engine_callback(schema)
    except Exception as exc:
        yield FatalError(exception=exc)


def load_schema(location: str, config: ProjectConfig) -> BaseSchema:
    """Load API schema automatically based on the provided configuration."""
    return _try_load_schema(location, config, _candidate_modules(location))


def _candidate_modules(location: str) -> tuple[ModuleType, ...]:
    """Loader modules to try for `location`, most likely first."""
    if is_grpc_location(location):
        return (grpc,)
    if is_openapi_file(location):
        return (openapi,)
    if is_probably_graphql(location):
        return (graphql, openapi)
    return (openapi, graphql)


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


def _try_load_schema(location: str, config: ProjectConfig, modules: tuple[ModuleType, ...]) -> BaseSchema:
    """Load with the first module, falling back to the rest in order."""
    from urllib3.exceptions import InsecureRequestWarning

    first, *rest = modules
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return _load_schema(location, config, first)
        except LoaderError as exc:
            if should_try_more(exc):
                for module in rest:
                    try:
                        return _load_schema(location, config, module)
                    except Exception as other_exc:
                        if is_specific_exception(other_exc):
                            raise other_exc
            # Re-raise the original error
            raise exc


def _load_schema(location: str, config: ProjectConfig, module: Any) -> BaseSchema:
    """Unified schema loader for both GraphQL and OpenAPI."""
    loader = detect_loader(location, module)

    kwargs: dict = {}
    if loader is module.from_url:
        if config.wait_for_schema is not None:
            kwargs["wait_for_schema"] = config.wait_for_schema
        kwargs.update(config.schema_request_kwargs())

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


def is_grpc_location(location: str) -> bool:
    if location.startswith(("grpc://", "grpcs://")):
        return True
    return location.endswith(GRPC_DESCRIPTOR_SET_EXTENSIONS) and file_exists(location)


def is_openapi_file(location: str) -> bool:
    name = os.path.basename(location).lower()
    return any(name == f"{base}{ext}" for base in ("openapi", "swagger") for ext in (".json", ".yaml", ".yml"))
