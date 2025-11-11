from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


def base_path_v2(raw_schema: Mapping[str, Any]) -> str:
    return raw_schema.get("basePath", "/")


def base_path_v3(raw_schema: Mapping[str, Any]) -> str:
    servers = raw_schema.get("servers", [])
    if servers:
        server = servers[0]
        url = server["url"].format(**{k: v["default"] for k, v in server.get("variables", {}).items()})
        return urlsplit(url).path or "/"
    return "/"
