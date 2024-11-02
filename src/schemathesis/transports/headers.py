from __future__ import annotations

from typing import Any

from ..constants import USER_AGENT


def setup_default_headers(kwargs: dict[str, Any]) -> None:
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT
