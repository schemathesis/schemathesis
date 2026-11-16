"""Automatic detection and loading of OpenAPI, GraphQL and gRPC schemas."""

from __future__ import annotations

import importlib
import inspect
import os
import sys
import warnings
from collections.abc import Callable, Iterable
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

import click

from schemathesis import auths, graphql, grpc, openapi
from schemathesis.cli.constants import MISSING_BASE_URL_MESSAGE
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.validation import validate_tls_verify
from schemathesis.config import ProjectConfig, ReportFormat
from schemathesis.core import Specification, SpecificationFeature
from schemathesis.core.app_output import inside_application
from schemathesis.core.errors import (
    HookExecutionError,
    IncorrectUsage,
    InvalidSchema,
    LoaderError,
    LoaderErrorKind,
    format_exception,
)
from schemathesis.core.fs import file_exists
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted
from schemathesis.grpc.loaders import BUILD_HINT, require_extra

if TYPE_CHECKING:
    from types import ModuleType

    from schemathesis.engine import events
    from schemathesis.schemas import BaseSchema

GRPC_HTTP_REPORTS_MESSAGE = (
    "HAR, VCR, WFC and pytest reports describe HTTP/1.1 requests and are not available for gRPC targets"
)
GRPC_WFC_AUTH_MESSAGE = (
    "Web Fuzzing Commons authentication (`--auth-wfc`, `[auth.wfc]`) is not available for gRPC targets"
)
GRPC_APP_MESSAGE = "`--app` works with Open API and GraphQL schemas, not with gRPC"
GRPC_REQUESTS_AUTH_MESSAGE = (
    "`requests` auth objects are not supported for gRPC targets; "
    "pass credentials as headers, basic auth or an `@schemathesis.auth()` class"
)
WFC_REPORT_MESSAGE = "WFC reports describe REST endpoints and are only available for Open API schemas"
# Reports that need a specification feature, checked in order, with the refusal for a schema that lacks it.
_REPORT_FEATURES: tuple[tuple[SpecificationFeature, tuple[ReportFormat, ...], str], ...] = (
    (
        SpecificationFeature.HTTP_REPORTS,
        (ReportFormat.HAR, ReportFormat.VCR, ReportFormat.WFC, ReportFormat.PYTEST),
        GRPC_HTTP_REPORTS_MESSAGE,
    ),
    (SpecificationFeature.WFC_REPORTS, (ReportFormat.WFC,), WFC_REPORT_MESSAGE),
)
GRPC_DESCRIPTOR_SET_EXTENSIONS = (".binpb", ".pb", ".protoset")
TEXT_SCHEMA_EXTENSIONS = (".json", ".yaml", ".yml")
PROTO_FILE_MESSAGE = f"Schemathesis reads descriptor sets, not `.proto` files. {BUILD_HINT} and run `schemathesis run api.binpb --url URL`"


def into_event_stream(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    engine_callback: Callable[[BaseSchema], Iterable[events.EngineEvent]],
) -> EventGenerator:
    validate_tls_verify(config.tls_verify)
    # Checked before any report handler creates its file.
    if is_grpc_location(location):
        if config.app is not None:
            raise click.UsageError(GRPC_APP_MESSAGE)
        _refuse_unsupported_reports(config, Specification.grpc(version=""))
        if config.auth.wfc is not None:
            raise click.UsageError(GRPC_WFC_AUTH_MESSAGE)
        if any(
            isinstance(provider, auths.SelectiveAuthProvider) and isinstance(provider.provider, auths.RequestsAuth)
            for provider in auths.GLOBAL_AUTH_STORAGE.providers
        ):
            raise click.UsageError(GRPC_REQUESTS_AUTH_MESSAGE)
        try:
            require_extra()
        except ImportError as exc:
            raise click.ClickException(str(exc)) from None
    if location.endswith(".proto") and file_exists(location):
        raise click.UsageError(PROTO_FILE_MESSAGE)
    return _event_stream(location=location, config=config, filter_set=filter_set, engine_callback=engine_callback)


def _refuse_unsupported_reports(config: ProjectConfig, specification: Specification) -> None:
    for feature, formats, message in _REPORT_FEATURES:
        if not specification.supports_feature(feature) and any(
            config.reports.get(report_format).enabled for report_format in formats
        ):
            raise click.UsageError(message)


def _event_stream(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    engine_callback: Callable[[BaseSchema], Iterable[events.EngineEvent]],
) -> EventGenerator:
    # The engine talks to handlers through events, so schema loading emits its own events too.
    loading_started = LoadingStarted(location=location)
    yield loading_started

    try:
        schema = load_schema(location=location, config=config)
        _refuse_unsupported_reports(config, schema.specification)
        # An explicitly configured base URL carries its own path, so the origin only applies without one.
        if schema.config.origin is not None and schema.config.base_url is None:
            schema.config.base_url = schema.config.origin.rstrip("/") + schema.base_path.rstrip("/")
        # Schemas select operations through a `FilterSet` combining config file filters and CLI options, not the config.
        schema.filter_set = schema.config.operations.create_filter_set(**filter_set)
        if file_exists(location) and schema.config.base_url is None and schema.app is None:
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
    if config.app is None:
        return _try_load_schema(location, config, _candidate_modules(location))
    with inside_application():
        app = load_app(config.app)
    return _try_load_schema(location, config, _candidate_modules(location), app)


def load_app(path: str) -> object:
    """Import the application at `module:attribute`."""
    module_name, _, attribute = path.partition(":")
    if not module_name or not attribute:
        raise click.UsageError(f"Invalid `--app` value `{path}`: expected `module:attribute`, e.g. `project.asgi:app`")
    # Applications often live in the working directory, which is not always importable
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.append(cwd)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name == module_name:
            raise click.UsageError(f"Can not import `{module_name}` from `--app={path}`: no such module") from None
        # The import machinery's own frames say nothing about the application
        trace = exc.__traceback__
        while trace is not None and (
            "importlib" in trace.tb_frame.f_code.co_filename or trace.tb_frame.f_code is load_app.__code__
        ):
            trace = trace.tb_next
        details = format_exception(exc.with_traceback(trace), with_traceback=True)
        raise click.UsageError(f"Can not import `{module_name}` from `--app={path}`:\n\n{details}") from None
    try:
        app = getattr(module, attribute)
    except AttributeError:
        raise click.UsageError(f"Module `{module_name}` from `--app={path}` has no attribute `{attribute}`") from None
    if _is_factory(app):
        try:
            app = app()
        except Exception as exc:
            details = format_exception(exc, with_traceback=True, skip_frames=1)
            raise click.UsageError(
                f"`{attribute}` from `--app={path}` failed to create the app:\n\n{details}"
            ) from None
    if not callable(app):
        raise click.UsageError(
            f"`{attribute}` from `--app={path}` is not an ASGI or WSGI application, got `{type(app).__name__}`"
        )
    return app


def _is_factory(app: object) -> bool:
    """Whether calling `app` with nothing builds the application: ASGI and WSGI apps need arguments."""
    if not callable(app):
        return False
    try:
        signature = inspect.signature(app)
    except (TypeError, ValueError):
        return False
    return all(
        parameter.default is not parameter.empty or parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


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

    # A connection failure would fail other loaders too
    return not isinstance(exc.__cause__, requests.exceptions.ConnectionError) and exc.kind not in (
        LoaderErrorKind.OPEN_API_INVALID_SCHEMA,
        LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION,
        LoaderErrorKind.OPEN_API_UNSUPPORTED_VERSION,
    )


def detect_loader(location: str, module: Any) -> Callable:
    if file_exists(location):
        return module.from_path
    return module.from_url


def _try_load_schema(
    location: str, config: ProjectConfig, modules: tuple[ModuleType, ...], app: object = None
) -> BaseSchema:
    """Load with the first module, falling back to the rest in order."""
    from urllib3.exceptions import InsecureRequestWarning

    first, *rest = modules
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return _load_schema(location, config, first, app)
        except LoaderError as exc:
            if should_try_more(exc):
                for module in rest:
                    try:
                        return _load_schema(location, config, module, app)
                    except Exception as other_exc:
                        if is_specific_exception(other_exc):
                            raise other_exc
            raise exc


def _load_schema(location: str, config: ProjectConfig, module: Any, app: object = None) -> BaseSchema:
    """Unified schema loader for OpenAPI, GraphQL, and gRPC."""
    if app is not None:
        if file_exists(location):
            if module is graphql:
                raise click.UsageError("A GraphQL `--app` needs the path it serves GraphQL at, e.g. `/graphql`")
            return openapi.loaders._from_path(location, config=config._parent, app=app)
        from schemathesis.transport import is_asgi_app

        loader = module.from_asgi if is_asgi_app(app) else module.from_wsgi
        try:
            return loader(location, app, config=config._parent)
        except (LoaderError, HookExecutionError):
            raise
        except Exception as exc:
            raise click.UsageError(
                f"The application failed while serving its schema at `{location}`: {type(exc).__name__}: {exc}"
            ) from None
    loader = detect_loader(location, module)

    kwargs: dict = {}
    if loader is module.from_url:
        if config.wait_for_schema is not None:
            kwargs["wait_for_schema"] = config.wait_for_schema
        kwargs.update(config.schema_request_kwargs())

    return loader(location, config=config._parent, **kwargs)


def is_specific_exception(exc: Exception) -> bool:
    """Whether a fallback loader's error is specific enough to report instead of the original one."""
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
    if not file_exists(location):
        return False
    if location.endswith(GRPC_DESCRIPTOR_SET_EXTENSIONS):
        return True
    # Descriptor sets may have any extension, but JSON and YAML files are text schemas even when malformed.
    return not location.lower().endswith(TEXT_SCHEMA_EXTENSIONS) and _is_binary_file(location)


def _is_binary_file(location: str) -> bool:
    with open(location, "rb") as file:
        data = file.read()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(byte < 32 and byte not in b"\t\n\r" for byte in data)


def is_openapi_file(location: str) -> bool:
    name = os.path.basename(location).lower()
    return any(name == f"{base}{ext}" for base in ("openapi", "swagger") for ext in (".json", ".yaml", ".yml"))
