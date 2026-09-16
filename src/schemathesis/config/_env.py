from __future__ import annotations

import os
import re
from typing import Any

from schemathesis.config._error import ConfigError

# `${NAME}` is a placeholder and `$${` is a literal `${`; any other `$` is kept as is.
PLACEHOLDER = re.compile(r"\$(?:(?P<escaped>\$)(?=\{)|\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<invalid>\{))")


def resolve(value: Any) -> Any:
    """Resolve environment variables in `${NAME}` placeholders."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        if match.group("escaped") is not None:
            return "$"
        if match.group("invalid") is not None:
            raise ConfigError(f"Invalid placeholder in string: `{value}`")
        try:
            return os.environ[match.group("name")]
        except KeyError:
            raise ConfigError(f"Missing environment variable: `{value}`") from None

    return PLACEHOLDER.sub(replace, value)
