from __future__ import annotations

from enum import Enum
from random import Random
from typing import TYPE_CHECKING

from schemathesis.checks import CHECKS, ChecksConfig
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
from schemathesis.generation.overrides import Override
from schemathesis.runner.config import EngineConfig, ExecutionConfig, NetworkConfig

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.generation.case import CheckFunction
    from schemathesis.generation.targets import TargetFunction
    from schemathesis.runner.core import Engine

    from ..schemas import BaseSchema


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    SKIP = "skip"

    def __lt__(self, other: Status) -> bool:  # type: ignore[override]
        return _STATUS_ORDER[self] < _STATUS_ORDER[other]


_STATUS_ORDER = {Status.SUCCESS: 0, Status.FAILURE: 1, Status.ERROR: 2, Status.INTERRUPTED: 3, Status.SKIP: 4}


def from_schema(
    schema: BaseSchema,
    *,
    override: Override | None = None,
    checks: list[CheckFunction] | None = None,
    targets: list[TargetFunction] | None = None,
    workers_num: int = 1,
    hypothesis_settings: hypothesis.settings | None = None,
    seed: int | None = None,
    no_failfast: bool = False,
    max_failures: int | None = None,
    unique_data: bool = False,
    network: NetworkConfig | None = None,
    checks_config: ChecksConfig | None = None,
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
            generation_config=schema.generation_config,
            max_failures=max_failures,
            no_failfast=no_failfast,
            unique_data=unique_data,
            seed=seed,
            workers_num=workers_num,
        ),
        network=network or NetworkConfig(),
        override=override,
        checks_config=checks_config,
    )
    return Engine(config=config)
