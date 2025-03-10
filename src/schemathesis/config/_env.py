from __future__ import annotations

import os
from string import Template
from typing import Any


def resolve(value: Any, default: Any) -> Any:
    """Resolve environment variables using string templates."""
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return Template(value).substitute(os.environ)
    except KeyError:
        return default
