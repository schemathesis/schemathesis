from __future__ import annotations

from random import Random
from typing import TYPE_CHECKING

from schemathesis.checks import CHECKS, ChecksConfig
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
from schemathesis.generation.overrides import Override

from ..generation import GenerationConfig
from .config import EngineConfig, ExecutionConfig, NetworkConfig

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.generation.case import CheckFunction
    from schemathesis.generation.targets import TargetFunction

    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from .core import Engine


def from_schema(
    schema: BaseSchema,
    *,
    override: Override | None = None,
    checks: list[CheckFunction] | None = None,
    targets: list[TargetFunction] | None = None,
    workers_num: int = 1,
    hypothesis_settings: hypothesis.settings | None = None,
    generation_config: GenerationConfig | None = None,
    seed: int | None = None,
    no_failfast: bool = False,
    max_failures: int | None = None,
    unique_data: bool = False,
    dry_run: bool = False,
    network: NetworkConfig | None = None,
    checks_config: ChecksConfig | None = None,
    service_client: ServiceClient | None = None,
) -> Engine:
    import hypothesis

    from .core import Engine

    checks = checks or CHECKS.get_all()
    checks_config = checks_config or {}

    hypothesis_settings = hypothesis_settings or hypothesis.settings(deadline=DEFAULT_DEADLINE)

    # Use the same seed for all tests unless `derandomize=True` is used
    if seed is None and not hypothesis_settings.derandomize:
        seed = Random().getrandbits(128)
    config = EngineConfig(
        schema=schema,
        execution=ExecutionConfig(
            checks=checks,
            targets=targets or [],
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config or schema.generation_config,
            max_failures=max_failures,
            no_failfast=no_failfast,
            unique_data=unique_data,
            dry_run=dry_run,
            seed=seed,
            workers_num=workers_num,
        ),
        network=network or NetworkConfig(),
        override=override,
        checks_config=checks_config,
        service_client=service_client,
    )
    return Engine(config=config)
