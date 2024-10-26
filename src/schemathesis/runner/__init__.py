from __future__ import annotations

from random import Random
from typing import TYPE_CHECKING, Any, Iterable

from ..constants import DEFAULT_DEADLINE, HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER
from ..generation import GenerationConfig
from ..internal.checks import CheckConfig
from ..internal.datetime import current_datetime
from ..targets import DEFAULT_TARGETS, Target
from ..transports import RequestConfig
from ..types import NotSet, RawAuth, RequestCert
from .probes import ProbeConfig

if TYPE_CHECKING:
    import hypothesis

    from .._override import CaseOverride
    from ..models import CheckFunction
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..stateful import Stateful
    from .impl import BaseRunner


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
    unique_data: bool = False,
    dry_run: bool = False,
    store_interactions: bool = False,
    stateful: Stateful | None = None,
    count_operations: bool = True,
    count_links: bool = True,
    probe_config: ProbeConfig | None = None,
    checks_config: CheckConfig | None = None,
    service_client: ServiceClient | None = None,
) -> BaseRunner:
    import hypothesis

    from ..checks import DEFAULT_CHECKS
    from ..transports.asgi import is_asgi_app
    from .impl import (
        SingleThreadASGIRunner,
        SingleThreadRunner,
        SingleThreadWSGIRunner,
        ThreadPoolASGIRunner,
        ThreadPoolRunner,
        ThreadPoolWSGIRunner,
    )

    checks = checks or DEFAULT_CHECKS
    checks_config = checks_config or CheckConfig()
    probe_config = probe_config or ProbeConfig()

    hypothesis_settings = hypothesis_settings or hypothesis.settings(deadline=DEFAULT_DEADLINE)
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
                unique_data=unique_data,
                dry_run=dry_run,
                store_interactions=store_interactions,
                stateful=stateful,
                count_operations=count_operations,
                count_links=count_links,
                probe_config=probe_config,
                checks_config=checks_config,
                service_client=service_client,
            )
        if is_asgi_app(schema.app):
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
                unique_data=unique_data,
                dry_run=dry_run,
                store_interactions=store_interactions,
                stateful=stateful,
                count_operations=count_operations,
                count_links=count_links,
                probe_config=probe_config,
                checks_config=checks_config,
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
            unique_data=unique_data,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            checks_config=checks_config,
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
            unique_data=unique_data,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            checks_config=checks_config,
            service_client=service_client,
        )
    if is_asgi_app(schema.app):
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
            unique_data=unique_data,
            dry_run=dry_run,
            store_interactions=store_interactions,
            stateful=stateful,
            count_operations=count_operations,
            count_links=count_links,
            probe_config=probe_config,
            checks_config=checks_config,
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
        unique_data=unique_data,
        dry_run=dry_run,
        store_interactions=store_interactions,
        stateful=stateful,
        count_operations=count_operations,
        count_links=count_links,
        probe_config=probe_config,
        checks_config=checks_config,
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
