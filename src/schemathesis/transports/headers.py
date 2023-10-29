from typing import Dict, Any

from ..constants import USER_AGENT


def setup_default_headers(kwargs: Dict[str, Any]) -> None:
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT
