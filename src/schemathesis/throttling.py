from __future__ import annotations
from typing import TYPE_CHECKING

from .exceptions import UsageError


if TYPE_CHECKING:
    from pyrate_limiter import Limiter


def parse_units(rate: str) -> tuple[int, int]:
    from pyrate_limiter import Duration

    try:
        limit, interval_text = rate.split("/")
        interval = {
            "s": Duration.SECOND,
            "m": Duration.MINUTE,
            "h": Duration.HOUR,
            "d": Duration.DAY,
        }.get(interval_text)
        if interval is None:
            raise invalid_rate(rate)
        return int(limit), interval
    except ValueError as exc:
        raise invalid_rate(rate) from exc


def invalid_rate(value: str) -> UsageError:
    return UsageError(
        f"Invalid rate limit value: `{value}`. Should be in form `limit/interval`. "
        "Example: `10/m` for 10 requests per minute."
    )


def build_limiter(rate: str) -> Limiter:
    from ._rate_limiter import Limiter, Rate

    limit, interval = parse_units(rate)
    rate = Rate(limit, interval)
    return Limiter(rate)
