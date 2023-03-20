from typing import Tuple

from pyrate_limiter import Duration, Limiter, RequestRate

from .exceptions import UsageError


def parse_units(rate: str) -> Tuple[int, int]:
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
    limit, interval = parse_units(rate)
    rate = RequestRate(limit, interval)
    return Limiter(rate)
