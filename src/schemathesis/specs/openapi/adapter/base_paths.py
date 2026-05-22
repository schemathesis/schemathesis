from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from schemathesis.core.errors import InvalidSchema
from schemathesis.specs.openapi.adapter.servers import format_server_url


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
    formatted = format_server_url(servers[0])
    return urlsplit(formatted).path or "/"
