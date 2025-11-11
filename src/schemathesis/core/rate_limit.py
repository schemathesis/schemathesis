from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from schemathesis.core.errors import InvalidRateLimit

if TYPE_CHECKING:
    from pyrate_limiter import Duration, Limiter


def ratelimit(rate_limiter: Limiter | None, base_url: str | None) -> AbstractContextManager[None]:
    """Limit the rate of sending generated requests."""
    label = urlparse(base_url).netloc
    if rate_limiter is not None:
        rate_limiter.try_acquire(label)
    return nullcontext()


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
            raise InvalidRateLimit(rate)
        return int(limit), interval
    except ValueError as exc:
        raise InvalidRateLimit(rate) from exc


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
    from pyrate_limiter import Limiter, Rate

    limit, interval = parse_units(rate)
    rate = Rate(limit, interval)
    return Limiter(rate, max_delay=_get_max_delay(limit, interval))
