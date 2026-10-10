from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext, ChecksConfig, collect_after_run_failures
from schemathesis.generation import overrides
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from schemathesis.checks import ResponseChecks
    from schemathesis.config import ProjectConfig
    from schemathesis.core.failures import Failure
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.run import PhaseName
    from schemathesis.schemas import APIOperation


class CheckContextSource(Protocol):
    @property
    def config(self) -> ProjectConfig: ...

    def get_transport_kwargs(self, operation: APIOperation | None = None) -> dict[str, Any]: ...


def run_after_run_checks(ctx: EngineContext) -> list[Failure]:
    """Run class-based checks' `after_run` once all testing is done and collect their failures."""
    checks = ctx.checks.for_run()
    if not checks:
        return []
    return collect_after_run_failures(ctx.config, checks, ctx.get_transport_kwargs())


@dataclass(slots=True)
class CheckContextData:
    """Per-operation configuration a check context is built from."""

    override: Override
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: ChecksConfig
    transport_kwargs: dict[str, Any]

    def to_check_context(
        self,
        *,
        recorder: ScenarioRecorder,
        response_checks: ResponseChecks,
        phase: PhaseName | None,
        auth_enforced_operations: set[str],
    ) -> CheckContext:
        return CheckContext(
            override=self.override,
            auth=self.auth,
            headers=self.headers,
            config=self.config,
            transport_kwargs=self.transport_kwargs,
            recorder=recorder,
            response_checks=response_checks,
            phase=phase,
            auth_enforced_operations=auth_enforced_operations,
        )


def collect_check_context_data(
    *, operation: APIOperation, ctx: CheckContextSource, phase: str | None
) -> CheckContextData:
    headers = ctx.config.headers_for(operation=operation)
    return CheckContextData(
        override=overrides.for_operation(ctx.config, operation=operation),
        auth=ctx.config.auth_for(operation=operation),
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=ctx.config.checks_config_for(operation=operation, phase=phase),
        transport_kwargs=ctx.get_transport_kwargs(operation=operation),
    )


@dataclass(slots=True)
class CheckContextCache:
    """Per-operation check context cache.

    Per-operation config is constant for the lifetime of a run; caching avoids repeated lookups.
    """

    _cache: dict[str, CheckContextData] = field(default_factory=dict)

    def get_or_create(self, *, operation: APIOperation, ctx: CheckContextSource, phase: str | None) -> CheckContextData:
        label = operation.label
        cached = self._cache.get(label)
        if cached is None:
            cached = collect_check_context_data(operation=operation, ctx=ctx, phase=phase)
            self._cache[label] = cached
        return cached
