from __future__ import annotations

from random import Random
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable
from urllib.parse import urlparse

from .._override import CaseOverride
from ..constants import (
    DEFAULT_DEADLINE,
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER,
)
from ..exceptions import SchemaError
from ..generation import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod, GenerationConfig
from ..internal.datetime import current_datetime
from ..internal.deprecation import deprecated_function
from ..internal.validation import file_exists
from ..loaders import load_app
from ..specs.graphql import loaders as gql_loaders
from ..specs.openapi import loaders as oas_loaders
from ..targets import DEFAULT_TARGETS, Target
from ..transports import RequestConfig
from ..transports.auth import get_requests_auth
from ..types import Filter, NotSet, RawAuth, RequestCert
from .probes import ProbeConfig

if TYPE_CHECKING:
    import hypothesis

    from ..models import CheckFunction
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..stateful import Stateful
    from . import events
    from .impl import BaseRunner


@deprecated_function(removed_in="4.0", replacement="schemathesis.runner.from_schema")
def prepare(
    schema_uri: str | dict[str, Any],
    *,
    # Runtime behavior
    checks: Iterable[CheckFunction] | None = None,
    data_generation_methods: tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: int | None = None,
    targets: Iterable[Target] = DEFAULT_TARGETS,
    workers_num: int = 1,
    seed: int | None = None,
    exit_first: bool = False,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Stateful | None = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    # Schema loading
    loader: Callable = oas_loaders.from_uri,
    base_url: str | None = None,
    auth: tuple[str, str] | None = None,
    auth_type: str | None = None,
    override: CaseOverride | None = None,
    headers: dict[str, str] | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool | str = True,
    request_cert: RequestCert | None = None,
    endpoint: Filter | None = None,
    method: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    app: str | None = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    force_schema_version: str | None = None,
    count_operations: bool = True,
    count_links: bool = True,
    # Hypothesis-specific configuration
    hypothesis_deadline: int | NotSet | None = None,
    hypothesis_derandomize: bool | None = None,
    hypothesis_max_examples: int | None = None,
    hypothesis_phases: list[hypothesis.Phase] | None = None,
    hypothesis_report_multiple_bugs: bool | None = None,
    hypothesis_suppress_health_check: list[hypothesis.HealthCheck] | None = None,
    hypothesis_verbosity: hypothesis.Verbosity | None = None,
    probe_config: ProbeConfig | None = None,
    service_client: ServiceClient | None = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Prepare a generator that will run test cases against the given API definition."""
    from ..checks import DEFAULT_CHECKS

    checks = checks or DEFAULT_CHECKS

    validate_loader(loader, schema_uri)

    if auth is None:
        # Auth type doesn't matter if auth is not passed
        auth_type = None  # type: ignore
    hypothesis_settings = prepare_hypothesis_settings(
        deadline=hypothesis_deadline,
        derandomize=hypothesis_derandomize,
        max_examples=hypothesis_max_examples,
        phases=hypothesis_phases,
        report_multiple_bugs=hypothesis_report_multiple_bugs,
        suppress_health_check=hypothesis_suppress_health_check,
        verbosity=hypothesis_verbosity,
    )
    return execute_from_schema(
        schema_uri=schema_uri,
        loader=loader,
        base_url=base_url,
        endpoint=endpoint,
        method=method,
        tag=tag,
        operation_id=operation_id,
        app=app,
        validate_schema=validate_schema,
        skip_deprecated_operations=skip_deprecated_operations,
        force_schema_version=force_schema_version,
        checks=checks,
        data_generation_methods=data_generation_methods,
        max_response_time=max_response_time,
        targets=targets,
        hypothesis_settings=hypothesis_settings,
        seed=seed,
        workers_num=workers_num,
        exit_first=exit_first,
        dry_run=dry_run,
        auth=auth,
        auth_type=auth_type,
        override=override,
        headers=headers,
        request_timeout=request_timeout,
        request_tls_verify=request_tls_verify,
        request_cert=request_cert,
        store_interactions=store_interactions,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        count_operations=count_operations,
        count_links=count_links,
        probe_config=probe_config,
        service_client=service_client,
    )


def validate_loader(loader: Callable, schema_uri: str | dict[str, Any]) -> None:
    """Sanity checking for input schema & loader."""
    if loader not in (
        oas_loaders.from_uri,
        oas_loaders.from_aiohttp,
        oas_loaders.from_dict,
        oas_loaders.from_file,
        oas_loaders.from_path,
        oas_loaders.from_asgi,
        oas_loaders.from_wsgi,
        gql_loaders.from_dict,
        gql_loaders.from_url,
        gql_loaders.from_wsgi,
    ):
        # Custom loaders are not checked
        return
    if isinstance(schema_uri, dict):
        if loader not in (oas_loaders.from_dict, gql_loaders.from_dict):
            raise ValueError("Dictionary as a schema is allowed only with `from_dict` loader")
    elif loader in (oas_loaders.from_dict, gql_loaders.from_dict):
        raise ValueError("Schema should be a dictionary for `from_dict` loader")


def execute_from_schema(
    *,
    schema_uri: str | dict[str, Any],
    loader: Callable = oas_loaders.from_uri,
    base_url: str | None = None,
    endpoint: Filter | None = None,
    method: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    app: str | None = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    force_schema_version: str | None = None,
    checks: Iterable[CheckFunction],
    data_generation_methods: tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: int | None = None,
    targets: Iterable[Target],
    workers_num: int = 1,
    hypothesis_settings: hypothesis.settings,
    auth: RawAuth | None = None,
    auth_type: str | None = None,
    override: CaseOverride | None = None,
    headers: dict[str, Any] | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool | str = True,
    request_cert: RequestCert | None = None,
    seed: int | None = None,
    exit_first: bool = False,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Stateful | None = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    count_operations: bool = True,
    count_links: bool = True,
    probe_config: ProbeConfig | None = None,
    service_client: ServiceClient | None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    try:
        if app is not None:
            app = load_app(app)
        schema = load_schema(
            schema_uri,
            base_url=base_url,
            loader=loader,
            app=app,
            validate_schema=validate_schema,
            skip_deprecated_operations=skip_deprecated_operations,
            auth=auth,
            auth_type=auth_type,
            headers=headers,
            endpoint=endpoint,
            method=method,
            tag=tag,
            operation_id=operation_id,
            data_generation_methods=data_generation_methods,
            force_schema_version=force_schema_version,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
        )
        yield from from_schema(
            schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            auth=auth,
            auth_type=auth_type,
            override=override,
            headers=headers,
            seed=seed,
            workers_num=workers_num,
            request_timeout=request_timeout,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            exit_first=exit_first,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            service_client=service_client,
        ).execute()
    except SchemaError as error:
        yield events.InternalError.from_schema_error(error)
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


def load_schema(
    schema_uri: str | dict[str, Any],
    *,
    base_url: str | None = None,
    loader: Callable = oas_loaders.from_uri,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    data_generation_methods: tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: str | None = None,
    request_tls_verify: bool | str = True,
    request_cert: RequestCert | None = None,
    # Network request parameters
    auth: tuple[str, str] | None = None,
    auth_type: str | None = None,
    headers: dict[str, str] | None = None,
    # Schema filters
    endpoint: Filter | None = None,
    method: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
) -> BaseSchema:
    """Load schema via specified loader and parameters."""
    loader_options: dict[str, Any] = {
        key: value
        for key, value in (
            ("base_url", base_url),
            ("endpoint", endpoint),
            ("method", method),
            ("tag", tag),
            ("operation_id", operation_id),
            ("app", app),
            ("data_generation_methods", data_generation_methods),
        )
        if value
    }

    if not isinstance(schema_uri, dict):
        if file_exists(schema_uri):
            loader = oas_loaders.from_path
        elif loader is not oas_loaders.from_path:
            if app is not None and not urlparse(schema_uri).netloc:
                # If `schema` is not an existing filesystem path, or a URL then it is considered as a path within
                # the given app
                loader = oas_loaders.get_loader_for_app(app)
                if headers:
                    loader_options["headers"] = headers
            else:
                if headers:
                    loader_options["headers"] = headers
                if auth:
                    loader_options["auth"] = auth
                if auth_type:
                    loader_options["auth_type"] = auth_type

    if loader is oas_loaders.from_uri and loader_options.get("auth"):
        loader_options["auth"] = get_requests_auth(loader_options["auth"], loader_options.pop("auth_type", None))
    if loader in (oas_loaders.from_uri, oas_loaders.from_aiohttp):
        loader_options["verify"] = request_tls_verify
        loader_options["cert"] = request_cert

    return loader(
        schema_uri,
        validate_schema=validate_schema,
        skip_deprecated_operations=skip_deprecated_operations,
        force_schema_version=force_schema_version,
        **loader_options,
    )


def from_schema(
    schema: BaseSchema,
    *,
    override: CaseOverride | None = None,
    checks: Iterable[CheckFunction] | None = None,
    max_response_time: int | None = None,
    targets: Iterable[Target] = DEFAULT_TARGETS,
    workers_num: int = 1,
    hypothesis_settings: hypothesis.settings | None = None,
    generation_config: GenerationConfig | None = None,
    auth: RawAuth | None = None,
    auth_type: str | None = None,
    headers: dict[str, Any] | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool | str = True,
    request_proxy: str | None = None,
    request_cert: RequestCert | None = None,
    seed: int | None = None,
    exit_first: bool = False,
    max_failures: int | None = None,
    started_at: str | None = None,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Stateful | None = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    count_operations: bool = True,
    count_links: bool = True,
    probe_config: ProbeConfig | None = None,
    service_client: ServiceClient | None = None,
) -> BaseRunner:
    import hypothesis
    from starlette.applications import Starlette

    from ..checks import DEFAULT_CHECKS
    from .impl import (
        SingleThreadASGIRunner,
        SingleThreadRunner,
        SingleThreadWSGIRunner,
        ThreadPoolASGIRunner,
        ThreadPoolRunner,
        ThreadPoolWSGIRunner,
    )

    checks = checks or DEFAULT_CHECKS
    probe_config = probe_config or ProbeConfig()

    hypothesis_settings = hypothesis_settings or hypothesis.settings(deadline=DEFAULT_DEADLINE)
    generation_config = generation_config or GenerationConfig()
    request_config = RequestConfig(
        timeout=request_timeout,
        tls_verify=request_tls_verify,
        proxy=request_proxy,
        cert=request_cert,
    )

    # Use the same seed for all tests unless `derandomize=True` is used
    if seed is None and not hypothesis_settings.derandomize:
        seed = Random().getrandbits(128)

    started_at = started_at or current_datetime()
    if workers_num > 1:
        if not schema.app:
            return ThreadPoolRunner(
                schema=schema,
                checks=checks,
                max_response_time=max_response_time,
                targets=targets,
                hypothesis_settings=hypothesis_settings,
                generation_config=generation_config,
                auth=auth,
                auth_type=auth_type,
                override=override,
                headers=headers,
                seed=seed,
                workers_num=workers_num,
                request_config=request_config,
                exit_first=exit_first,
                max_failures=max_failures,
                started_at=started_at,
                dry_run=dry_run,
                store_interactions=store_interactions,
                stateful=stateful,
                stateful_recursion_limit=stateful_recursion_limit,
                count_operations=count_operations,
                count_links=count_links,
                probe_config=probe_config,
                service_client=service_client,
            )
        if isinstance(schema.app, Starlette):
            return ThreadPoolASGIRunner(
                schema=schema,
                checks=checks,
                max_response_time=max_response_time,
                targets=targets,
                hypothesis_settings=hypothesis_settings,
                generation_config=generation_config,
                auth=auth,
                auth_type=auth_type,
                override=override,
                headers=headers,
                seed=seed,
                exit_first=exit_first,
                max_failures=max_failures,
                started_at=started_at,
                dry_run=dry_run,
                store_interactions=store_interactions,
                stateful=stateful,
                stateful_recursion_limit=stateful_recursion_limit,
                count_operations=count_operations,
                count_links=count_links,
                probe_config=probe_config,
                service_client=service_client,
            )
        return ThreadPoolWSGIRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config,
            auth=auth,
            auth_type=auth_type,
            override=override,
            headers=headers,
            seed=seed,
            workers_num=workers_num,
            exit_first=exit_first,
            max_failures=max_failures,
            started_at=started_at,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            service_client=service_client,
        )
    if not schema.app:
        return SingleThreadRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config,
            auth=auth,
            auth_type=auth_type,
            override=override,
            headers=headers,
            seed=seed,
            request_config=request_config,
            exit_first=exit_first,
            max_failures=max_failures,
            started_at=started_at,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            service_client=service_client,
        )
    if isinstance(schema.app, Starlette):
        return SingleThreadASGIRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config,
            auth=auth,
            auth_type=auth_type,
            override=override,
            headers=headers,
            seed=seed,
            exit_first=exit_first,
            max_failures=max_failures,
            started_at=started_at,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            service_client=service_client,
        )
    return SingleThreadWSGIRunner(
        schema=schema,
        checks=checks,
        max_response_time=max_response_time,
        targets=targets,
        hypothesis_settings=hypothesis_settings,
        generation_config=generation_config,
        auth=auth,
        auth_type=auth_type,
        override=override,
        headers=headers,
        seed=seed,
        exit_first=exit_first,
        max_failures=max_failures,
        started_at=started_at,
        dry_run=dry_run,
        store_interactions=store_interactions,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        count_operations=count_operations,
        count_links=count_links,
        probe_config=probe_config,
        service_client=service_client,
    )


def prepare_hypothesis_settings(
    database: str | None = None,
    deadline: int | NotSet | None = None,
    derandomize: bool | None = None,
    max_examples: int | None = None,
    phases: list[hypothesis.Phase] | None = None,
    report_multiple_bugs: bool | None = None,
    suppress_health_check: list[hypothesis.HealthCheck] | None = None,
    verbosity: hypothesis.Verbosity | None = None,
) -> hypothesis.settings:
    import hypothesis
    from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

    kwargs = {
        key: value
        for key, value in (
            ("derandomize", derandomize),
            ("max_examples", max_examples),
            ("phases", phases),
            ("report_multiple_bugs", report_multiple_bugs),
            ("suppress_health_check", suppress_health_check),
            ("verbosity", verbosity),
        )
        if value is not None
    }
    # `deadline` is special, since Hypothesis allows passing `None`
    if deadline is not None:
        if isinstance(deadline, NotSet):
            kwargs["deadline"] = None
        else:
            kwargs["deadline"] = deadline
    if database is not None:
        if database.lower() == "none":
            kwargs["database"] = None
        elif database == HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER:
            kwargs["database"] = InMemoryExampleDatabase()
        else:
            kwargs["database"] = DirectoryBasedExampleDatabase(database)
    kwargs.setdefault("deadline", DEFAULT_DEADLINE)
    return hypothesis.settings(print_blob=False, **kwargs)
