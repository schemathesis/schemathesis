from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from schemathesis.core.errors import InvalidSchema


def format_server_url(
    server: object, *, scope_label: str = "servers[0]", variables: Mapping[str, str] | None = None
) -> str:
    """Validate a Server Object and return its formatted ``url``.

    ``scope_label`` is the JSON-pointer-ish prefix used in error messages,
    e.g. ``servers[0]`` (global) or ``operation.servers[0]`` (per-scope).

    ``variables`` are user-supplied overrides that take precedence over spec defaults.
    """
    if not isinstance(server, dict):
        raise InvalidSchema(f"'{scope_label}' must be a server object")
    url = server.get("url")
    if not isinstance(url, str):
        raise InvalidSchema(f"'{scope_label}.url' must be a string")
    spec_variables = server.get("variables", {})
    if not isinstance(spec_variables, dict):
        raise InvalidSchema(f"'{scope_label}.variables' must be a mapping")
    defaults: dict[str, Any] = {}
    for name, spec in spec_variables.items():
        if variables and name in variables:
            defaults[name] = variables[name]
        elif not isinstance(spec, dict) or "default" not in spec:
            raise InvalidSchema(f"'{scope_label}.variables.{name}' must be an object with a 'default' field")
        else:
            defaults[name] = spec["default"]
    if variables:
        for name, value in variables.items():
            if name not in defaults:
                defaults[name] = value
    try:
        return url.format(**defaults)
    except (KeyError, IndexError) as exc:
        raise InvalidSchema(f"'{scope_label}.url' references undefined variable: {exc}") from exc


def resolve_operation_base_url(
    *,
    operation: Mapping[str, Any],
    path_item: Mapping[str, Any] | None,
    fallback_base_url: str,
    location: str | None,
    variables: Mapping[str, str] | None = None,
) -> str:
    """Compute the effective base URL for one operation.

    Walks operation -> path-item -> ``fallback_base_url``. Server variables are
    substituted using each variable's spec ``default``, with ``variables``
    (user-supplied overrides) taking precedence. Absolute URLs are returned
    as-is; relative URLs are combined with ``location``'s scheme and host.
    """
    server, scope_label = _select_server(operation, path_item)
    if server is None:
        return fallback_base_url
    formatted = format_server_url(server, scope_label=scope_label, variables=variables)
    parts = urlsplit(formatted)
    if parts.netloc:
        return formatted
    location_parts = urlsplit(location or "")
    return urlunsplit((location_parts.scheme, location_parts.netloc, parts.path or "/", "", ""))


def _select_server(
    operation: Mapping[str, Any], path_item: Mapping[str, Any] | None
) -> tuple[object, str] | tuple[None, str]:
    for source, label in ((operation, "operation.servers"), (path_item, "path.servers")):
        if source is None:
            continue
        servers = source.get("servers")
        if servers is None:
            continue
        if not isinstance(servers, list):
            raise InvalidSchema(f"'{label}' must be a list of server objects")
        if servers:
            return servers[0], f"{label}[0]"
    return None, ""
