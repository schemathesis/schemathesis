from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from schemathesis.core.errors import InvalidRateLimit

if TYPE_CHECKING:
    from pyrate_limiter import Limiter


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


def build_limiter(rate: str) -> Limiter:
    from pyrate_limiter import Limiter, Rate

    limit, interval = parse_units(rate)
    rate = Rate(limit, interval)
    return Limiter(rate)
