from __future__ import annotations

from random import Random
from typing import TYPE_CHECKING, Iterable

from ..constants import DEFAULT_DEADLINE
from ..generation import GenerationConfig
from ..internal.checks import CheckConfig
from ..targets import DEFAULT_TARGETS, Target
from .config import EngineConfig, ExecutionConfig, NetworkConfig

if TYPE_CHECKING:
    import hypothesis

    from .._override import CaseOverride
    from ..models import CheckFunction
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..stateful import Stateful
    from .core import Engine


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
    seed: int | None = None,
    max_failures: int | None = None,
    unique_data: bool = False,
    dry_run: bool = False,
    stateful: Stateful | None = None,
    network: NetworkConfig | None = None,
    checks_config: CheckConfig | None = None,
    service_client: ServiceClient | None = None,
) -> Engine:
    import hypothesis

    from ..checks import DEFAULT_CHECKS
    from .core import Engine

    checks = checks or DEFAULT_CHECKS
    checks_config = checks_config or CheckConfig()

    hypothesis_settings = hypothesis_settings or hypothesis.settings(deadline=DEFAULT_DEADLINE)

    # Use the same seed for all tests unless `derandomize=True` is used
    if seed is None and not hypothesis_settings.derandomize:
        seed = Random().getrandbits(128)
    config = EngineConfig(
        schema=schema,
        execution=ExecutionConfig(
            checks=checks,
            targets=targets,
            hypothesis_settings=hypothesis_settings,
            max_response_time=max_response_time,
            generation_config=generation_config,
            max_failures=max_failures,
            unique_data=unique_data,
            dry_run=dry_run,
            seed=seed,
            stateful=stateful,
            workers_num=workers_num,
        ),
        network=network or NetworkConfig(),
        override=override,
        checks_config=checks_config,
        service_client=service_client,
    )
    return Engine(config=config)
