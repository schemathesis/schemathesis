from __future__ import annotations

from typing import TYPE_CHECKING

from ._dependency_versions import IS_PYRATE_LIMITER_ABOVE_3
from .exceptions import UsageError

if TYPE_CHECKING:
    from pyrate_limiter import Duration, Limiter


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


def _get_max_delay(value: int, unit: Duration) -> int:
    from pyrate_limiter import Duration

    if unit == Duration.SECOND:
        multiplier = 1
    elif unit == Duration.MINUTE:
        multiplier = 60
    elif unit == Duration.HOUR:
        multiplier = 60 * 60
    else:
        multiplier = 60 * 60 * 24
    # Delay is in milliseconds + `pyrate_limiter` adds 50ms on top.
    # Hence adding 100 covers this
    return value * multiplier * 1000 + 100


def build_limiter(rate: str) -> Limiter:
    from ._rate_limiter import Limiter, Rate

    limit, interval = parse_units(rate)
    rate = Rate(limit, interval)
    kwargs = {}
    if IS_PYRATE_LIMITER_ABOVE_3:
        kwargs["max_delay"] = _get_max_delay(limit, interval)
    return Limiter(rate, **kwargs)
