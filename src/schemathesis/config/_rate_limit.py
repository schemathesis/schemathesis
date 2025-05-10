from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.config._error import ConfigError
from schemathesis.core import rate_limit
from schemathesis.core.errors import InvalidRateLimit

if TYPE_CHECKING:
    from pyrate_limiter import Limiter


def build_limiter(value: str) -> Limiter:
    try:
        return rate_limit.build_limiter(value)
    except InvalidRateLimit as exc:
        raise ConfigError(str(exc)) from None
