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


def base_url_v3(raw_schema: Mapping[str, Any]) -> str | None:
    servers = raw_schema.get("servers")
    if not isinstance(servers, list) or not servers:
        return None
    formatted = format_server_url(servers[0])
    return formatted if urlsplit(formatted).netloc else None


def base_url_v2(raw_schema: Mapping[str, Any]) -> str | None:
    host = raw_schema.get("host")
    if not isinstance(host, str) or not host:
        return None
    schemes = raw_schema.get("schemes") or ["https"]
    if not isinstance(schemes, list) or not isinstance(schemes[0], str):
        return None
    return f"{schemes[0]}://{host}{base_path_v2(raw_schema)}"
