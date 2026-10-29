from __future__ import annotations

from base64 import urlsafe_b64encode
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
    return {
        # The JSON mapping decodes bytes with the URL-safe alphabet.
        "byte": st.binary().map(lambda value: urlsafe_b64encode(value).decode()),
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
    }
