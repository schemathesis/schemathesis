from __future__ import annotations

import os
from string import Template
from typing import Any


def resolve(value: str | None, default: Any) -> Any:
    """Resolve environment variables using string templates."""
    if value is None:
        return default
    try:
        return Template(value).substitute(os.environ)
    except KeyError:
        return default
