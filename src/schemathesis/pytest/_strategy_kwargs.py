from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.generation import overrides

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.schemas import APIOperation


def strategy_kwargs_from_config(config: ProjectConfig, operation: APIOperation) -> dict[str, dict[str, str]]:
    """Fixed parameter values from the project config; configured headers win over header overrides."""
    kwargs = overrides.for_operation(config, operation=operation).as_strategy_kwargs()
    headers = dict(kwargs.get("headers", {}))
    headers.update(config.headers_for(operation=operation))
    if headers:
        kwargs["headers"] = headers
    return kwargs
