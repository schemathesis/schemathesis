from __future__ import annotations
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union, TYPE_CHECKING
from urllib.parse import urlparse

from ..generation import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod
from ..constants import (
    DEFAULT_DEADLINE,
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER,
)
from ..internal.deprecation import deprecated_function
from ..internal.datetime import current_datetime
from ..internal.validation import file_exists
from ..transports.auth import get_requests_auth
from ..exceptions import SchemaError
from ..loaders import load_app
from ..specs.graphql import loaders as gql_loaders
from ..specs.openapi import loaders as oas_loaders
from ..targets import DEFAULT_TARGETS, Target
from ..types import Filter, NotSet, RawAuth, RequestCert

if TYPE_CHECKING:
    from . import events
    from ..models import CheckFunction
    from ..schemas import BaseSchema
    from .impl import BaseRunner
    from ..stateful import Stateful
    import hypothesis


@deprecated_function(removed_in="4.0", replacement="schemathesis.runner.from_schema")
def prepare(
    schema_uri: Union[str, Dict[str, Any]],
    *,
    # Runtime behavior
    checks: Optional[Iterable[CheckFunction]] = None,
    data_generation_methods: Tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: Optional[int] = None,
    targets: Iterable[Target] = DEFAULT_TARGETS,
    workers_num: int = 1,
    seed: Optional[int] = None,
    exit_first: bool = False,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Optional[Stateful] = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    # Schema loading
    loader: Callable = oas_loaders.from_uri,
    base_url: Optional[str] = None,
    auth: Optional[Tuple[str, str]] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    request_timeout: Optional[int] = None,
    request_tls_verify: Union[bool, str] = True,
    request_cert: Optional[RequestCert] = None,
    endpoint: Optional[Filter] = None,
    method: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    app: Optional[str] = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    force_schema_version: Optional[str] = None,
    count_operations: bool = True,
    # Hypothesis-specific configuration
    hypothesis_deadline: Optional[Union[int, NotSet]] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
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
        headers=headers,
        request_timeout=request_timeout,
        request_tls_verify=request_tls_verify,
        request_cert=request_cert,
        store_interactions=store_interactions,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        count_operations=count_operations,
    )


def validate_loader(loader: Callable, schema_uri: Union[str, Dict[str, Any]]) -> None:
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
    schema_uri: Union[str, Dict[str, Any]],
    loader: Callable = oas_loaders.from_uri,
    base_url: Optional[str] = None,
    endpoint: Optional[Filter] = None,
    method: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    app: Optional[str] = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    force_schema_version: Optional[str] = None,
    checks: Iterable[CheckFunction],
    data_generation_methods: Tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: Optional[int] = None,
    targets: Iterable[Target],
    workers_num: int = 1,
    hypothesis_settings: hypothesis.settings,
    auth: Optional[RawAuth] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, Any]] = None,
    request_timeout: Optional[int] = None,
    request_tls_verify: Union[bool, str] = True,
    request_cert: Optional[RequestCert] = None,
    seed: Optional[int] = None,
    exit_first: bool = False,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Optional[Stateful] = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    count_operations: bool = True,
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
        ).execute()
    except SchemaError as error:
        yield events.InternalError.from_schema_error(error)
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


def load_schema(
    schema_uri: Union[str, Dict[str, Any]],
    *,
    base_url: Optional[str] = None,
    loader: Callable = oas_loaders.from_uri,
    app: Any = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    data_generation_methods: Tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    force_schema_version: Optional[str] = None,
    request_tls_verify: Union[bool, str] = True,
    request_cert: Optional[RequestCert] = None,
    # Network request parameters
    auth: Optional[Tuple[str, str]] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    # Schema filters
    endpoint: Optional[Filter] = None,
    method: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
) -> BaseSchema:
    """Load schema via specified loader and parameters."""
    loader_options = {
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
    checks: Optional[Iterable[CheckFunction]] = None,
    max_response_time: Optional[int] = None,
    targets: Iterable[Target] = DEFAULT_TARGETS,
    workers_num: int = 1,
    hypothesis_settings: Optional[hypothesis.settings] = None,
    auth: Optional[RawAuth] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, Any]] = None,
    request_timeout: Optional[int] = None,
    request_tls_verify: Union[bool, str] = True,
    request_cert: Optional[RequestCert] = None,
    seed: Optional[int] = None,
    exit_first: bool = False,
    max_failures: Optional[int] = None,
    started_at: Optional[str] = None,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Optional[Stateful] = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    count_operations: bool = True,
) -> BaseRunner:
    from starlette.applications import Starlette
    import hypothesis
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

    hypothesis_settings = hypothesis_settings or hypothesis.settings(deadline=DEFAULT_DEADLINE)
    started_at = started_at or current_datetime()
    if workers_num > 1:
        if not schema.app:
            return ThreadPoolRunner(
                schema=schema,
                checks=checks,
                max_response_time=max_response_time,
                targets=targets,
                hypothesis_settings=hypothesis_settings,
                auth=auth,
                auth_type=auth_type,
                headers=headers,
                seed=seed,
                workers_num=workers_num,
                request_timeout=request_timeout,
                request_tls_verify=request_tls_verify,
                request_cert=request_cert,
                exit_first=exit_first,
                max_failures=max_failures,
                started_at=started_at,
                dry_run=dry_run,
                store_interactions=store_interactions,
                stateful=stateful,
                stateful_recursion_limit=stateful_recursion_limit,
                count_operations=count_operations,
            )
        if isinstance(schema.app, Starlette):
            return ThreadPoolASGIRunner(
                schema=schema,
                checks=checks,
                max_response_time=max_response_time,
                targets=targets,
                hypothesis_settings=hypothesis_settings,
                auth=auth,
                auth_type=auth_type,
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
            )
        return ThreadPoolWSGIRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            auth=auth,
            auth_type=auth_type,
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
        )
    if not schema.app:
        return SingleThreadRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            auth=auth,
            auth_type=auth_type,
            headers=headers,
            seed=seed,
            request_timeout=request_timeout,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            exit_first=exit_first,
            max_failures=max_failures,
            started_at=started_at,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            count_operations=count_operations,
        )
    if isinstance(schema.app, Starlette):
        return SingleThreadASGIRunner(
            schema=schema,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            auth=auth,
            auth_type=auth_type,
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
        )
    return SingleThreadWSGIRunner(
        schema=schema,
        checks=checks,
        max_response_time=max_response_time,
        targets=targets,
        hypothesis_settings=hypothesis_settings,
        auth=auth,
        auth_type=auth_type,
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
    )


def prepare_hypothesis_settings(
    database: Optional[str] = None,
    deadline: Optional[Union[int, NotSet]] = None,
    derandomize: Optional[bool] = None,
    max_examples: Optional[int] = None,
    phases: Optional[List[hypothesis.Phase]] = None,
    report_multiple_bugs: Optional[bool] = None,
    suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    verbosity: Optional[hypothesis.Verbosity] = None,
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
