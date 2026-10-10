from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.generation import overrides

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.schemas import APIOperation


def strategy_kwargs_from_config(config: ProjectConfig, operation: APIOperation) -> dict[str, dict[str, str]]:
    """Fixed parameter values from the project config; configured headers win over header overrides."""
    kwargs: dict[str, dict[str, str]] = {
        location.container_name: entry
        for location, entry in overrides.for_operation(config, operation=operation).items()
    }
    headers = dict(kwargs.get("headers", {}))
    auth = config.auth_for(operation=operation)
    if auth is not None:
        from requests.auth import _basic_auth_str

        headers["Authorization"] = _basic_auth_str(*auth)
    headers.update(config.headers_for(operation=operation))
    if headers:
        kwargs["headers"] = headers
    return kwargs
