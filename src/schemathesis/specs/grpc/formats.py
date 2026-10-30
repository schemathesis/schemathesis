from __future__ import annotations

import ipaddress
from base64 import b64encode
from datetime import datetime
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

# Longest `google.protobuf.Duration`, about 10000 years.
MAX_DURATION_SECONDS = 315_576_000_000


@cache
def grpc_formats() -> dict[str, SearchStrategy[str]]:
    """Strategies for the formats the converter emits, limited to what the JSON mapping parses."""
    from hypothesis import strategies as st

    fraction = st.from_regex(r"(\.[0-9]{1,9})?", fullmatch=True)
    # Lowercase labels and a letters-only top-level label pass every hostname check the validation rules run.
    hostname = st.from_regex(r"([a-z0-9]{1,8}\.){1,2}[a-z]{2,6}", fullmatch=True)
    return {
        # The JSON mapping accepts both alphabets, command-line clients only the standard one.
        "byte": st.binary().map(lambda value: b64encode(value).decode()),
        # `Timestamp` spans years 0001 to 9999, in UTC.
        "date-time": st.builds(
            lambda moment, digits: f"{moment.isoformat(timespec='seconds')}{digits}Z",
            st.datetimes(min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59)),
            fraction,
        ),
        "_duration": st.builds(
            lambda seconds, digits: f"{seconds}{digits}s",
            st.integers(-MAX_DURATION_SECONDS, MAX_DURATION_SECONDS),
            fraction,
        ),
        # Formats of the validation rules on string fields.
        "email": st.builds(
            "{}@{}".format, st.from_regex(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]{1,10}", fullmatch=True), hostname
        ),
        "hostname": hostname,
        "ipv4": st.integers(0, 2**32 - 1).map(lambda packed: str(ipaddress.IPv4Address(packed))),
        "ipv6": st.integers(0, 2**128 - 1).map(lambda packed: str(ipaddress.IPv6Address(packed))),
        "uri": hostname.map("https://{}".format),
        "uri-reference": hostname.map("https://{}".format),
        "uuid": st.uuids().map(str),
    }
