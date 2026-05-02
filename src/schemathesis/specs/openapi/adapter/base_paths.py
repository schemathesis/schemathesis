from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from schemathesis.core.errors import InvalidSchema


def base_path_v2(raw_schema: Mapping[str, Any]) -> str:
    base_path = raw_schema.get("basePath", "/")
    if not isinstance(base_path, str):
        raise InvalidSchema("'basePath' must be a string")
    return base_path


def base_path_v3(raw_schema: Mapping[str, Any]) -> str:
    servers = raw_schema.get("servers", [])
    if not servers:
        return "/"
    if not isinstance(servers, list):
        raise InvalidSchema("'servers' must be a list of server objects")
    server = servers[0]
    if not isinstance(server, dict):
        raise InvalidSchema("'servers[0]' must be a server object")
    url = server.get("url")
    if not isinstance(url, str):
        raise InvalidSchema("'servers[0].url' must be a string")
    variables = server.get("variables", {})
    if not isinstance(variables, dict):
        raise InvalidSchema("'servers[0].variables' must be a mapping")
    defaults: dict[str, Any] = {}
    for name, spec in variables.items():
        if not isinstance(spec, dict) or "default" not in spec:
            raise InvalidSchema(f"'servers[0].variables.{name}' must be an object with a 'default' field")
        defaults[name] = spec["default"]
    try:
        formatted = url.format(**defaults)
    except (KeyError, IndexError) as exc:
        raise InvalidSchema(f"'servers[0].url' references undefined variable: {exc}") from exc
    return urlsplit(formatted).path or "/"
