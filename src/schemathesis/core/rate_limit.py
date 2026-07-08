from __future__ import annotations

import time
from contextlib import AbstractContextManager, nullcontext
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from schemathesis.core.errors import InvalidRateLimit

if TYPE_CHECKING:
    from pyrate_limiter import Limiter

RATE_LIMIT_AUTO_MAX_WAIT = 60
RATE_LIMIT_AUTO_MAX_RETRIES = 3
RATE_LIMIT_AUTO_REPORT_THRESHOLD = 5


def ratelimit(rate_limiter: Limiter | str | None, base_url: str | None) -> AbstractContextManager[None]:
    """Limit the rate of sending generated requests."""
    from pyrate_limiter import Limiter

    label = urlparse(base_url).netloc
    if isinstance(rate_limiter, Limiter):
        rate_limiter.try_acquire(label)
    return nullcontext()


def parse_retry_after(header_value: str) -> float | None:
    """Parse a Retry-After header value into seconds to wait.

    Handles both RFC 7231 formats:
    - Delay-seconds: "120" -> 120.0
    - HTTP-date: "Fri, 31 Dec 1999 23:59:59 GMT" -> seconds until that time

    Returns None on any parse failure. Result is clamped to [0, RATE_LIMIT_AUTO_MAX_WAIT].
    """
    if not header_value:
        return None

    try:
        seconds = float(header_value)
        return max(0.0, min(float(RATE_LIMIT_AUTO_MAX_WAIT), seconds))
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(header_value)
        remaining = target.timestamp() - time.time()
        return max(0.0, min(float(RATE_LIMIT_AUTO_MAX_WAIT), remaining))
    except Exception:
        return None


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
