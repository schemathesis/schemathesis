from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from requests.structures import CaseInsensitiveDict

from schemathesis.checks import ChecksConfig, collect_after_run_failures
from schemathesis.generation import overrides
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from schemathesis.core.failures import Failure
    from schemathesis.engine.context import EngineContext
    from schemathesis.schemas import APIOperation


def run_after_run_checks(ctx: EngineContext) -> list[Failure]:
    """Run class-based checks' `after_run` once all testing is done and collect their failures."""
    checks = ctx.checks.for_run()
    if not checks:
        return []
    return collect_after_run_failures(ctx.config, checks, ctx.get_transport_kwargs())


@dataclass(slots=True)
class CachedCheckContextData:
    override: Override
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: ChecksConfig
    transport_kwargs: dict[str, object]


@dataclass(slots=True)
class CheckContextCache:
    """Per-operation check context cache.

    Per-operation config is constant for the lifetime of a run; caching avoids repeated lookups.
    """

    _cache: dict[str, CachedCheckContextData] = field(default_factory=dict)

    def get_or_create(
        self, *, operation: APIOperation, ctx: EngineContext, phase: str | None
    ) -> CachedCheckContextData:
        label = operation.label
        cached = self._cache.get(label)
        if cached is None:
            headers = ctx.config.headers_for(operation=operation)
            cached = CachedCheckContextData(
                override=overrides.for_operation(ctx.config, operation=operation),
                auth=ctx.config.auth_for(operation=operation),
                headers=CaseInsensitiveDict(headers) if headers else None,
                config=ctx.config.checks_config_for(operation=operation, phase=phase),
                transport_kwargs=ctx.get_transport_kwargs(operation=operation),
            )
            self._cache[label] = cached
        return cached
