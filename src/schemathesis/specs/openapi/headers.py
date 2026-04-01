from __future__ import annotations

from datetime import timezone
from email.utils import format_datetime
from typing import TYPE_CHECKING

from schemathesis.specs.openapi.formats import header_values

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from schemathesis.generation import GenerationMode

KNOWN_HEADER_FORMATS: dict[str, str] = {
    "if-match": "_if_match_header",
    "if-none-match": "_if_match_header",
    "if-modified-since": "_http_date_header",
    "if-unmodified-since": "_http_date_header",
    "range": "_range_header",
}

# Visible ASCII for ETag content: 0x21-0x7E excluding DQUOTE (0x22)
_ETAG_CHARS = "".join(chr(i) for i in range(0x21, 0x7F) if chr(i) != '"')

STRUCTURED_HEADER_PROBABILITY = 0.75


def if_match_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    etag_content = st.text(alphabet=_ETAG_CHARS, max_size=20)
    strong = etag_content.map(lambda s: f'"{s}"')
    weak = etag_content.map(lambda s: f'W/"{s}"')
    single = st.one_of(st.just("*"), strong, weak)
    multi = st.lists(st.one_of(strong, weak), min_size=2, max_size=3).map(lambda tags: ", ".join(tags))
    return st.one_of(single, multi)


def http_date_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    return st.datetimes(timezones=st.just(timezone.utc)).map(lambda dt: format_datetime(dt, usegmt=True))


def _range_spec() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    non_neg = st.integers(min_value=0, max_value=10_000)
    int_range = non_neg.flatmap(
        lambda first: st.integers(min_value=first, max_value=first + 10_000).map(lambda last: f"{first}-{last}")
    )
    suffix_range = st.integers(min_value=1, max_value=10_000).map(lambda n: f"-{n}")
    open_ended = non_neg.map(lambda first: f"{first}-")
    return st.one_of(int_range, suffix_range, open_ended)


def range_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    single = _range_spec().map(lambda s: f"bytes={s}")
    multi = st.lists(_range_spec(), min_size=2, max_size=3).map(lambda specs: "bytes=" + ",".join(specs))
    return st.one_of(single, multi)


def range_slightly_invalid_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    non_neg = st.integers(min_value=0, max_value=10_000)

    # bytes=LAST-FIRST where LAST > FIRST (inverted bounds)
    inverted = non_neg.flatmap(
        lambda last: st.integers(min_value=last + 1, max_value=last + 10_000).map(lambda first: f"bytes={first}-{last}")
    )
    # bytes=-1-N (negative first byte position)
    neg_first = non_neg.map(lambda n: f"bytes=-1-{n}")
    # non-bytes unit and empty range-set as sampled constants
    constants = st.sampled_from(["invalid=0-100", "bytes="])

    return st.one_of(inverted, neg_first, constants)


def get_header_format_strategies(mode: GenerationMode) -> dict[str, st.SearchStrategy[str]]:
    from hypothesis import strategies as st

    if mode.is_positive:
        return {
            "_if_match_header": if_match_values(),
            "_http_date_header": http_date_values(),
            "_range_header": range_values(),
        }

    @st.composite  # type: ignore[untyped-decorator]
    def mixed_if_match(draw: st.DrawFn) -> str:
        rng = draw(st.randoms())
        if rng.random() < STRUCTURED_HEADER_PROBABILITY:
            return draw(if_match_values())
        return draw(header_values())

    @st.composite  # type: ignore[untyped-decorator]
    def mixed_http_date(draw: st.DrawFn) -> str:
        rng = draw(st.randoms())
        if rng.random() < STRUCTURED_HEADER_PROBABILITY:
            return draw(http_date_values())
        return draw(header_values())

    @st.composite  # type: ignore[untyped-decorator]
    def mixed_range(draw: st.DrawFn) -> str:
        rng = draw(st.randoms())
        r = rng.random()
        if r < 0.50:
            return draw(range_values())
        elif r < 0.75:
            return draw(range_slightly_invalid_values())
        return draw(header_values())

    return {
        "_if_match_header": mixed_if_match(),
        "_http_date_header": mixed_http_date(),
        "_range_header": mixed_range(),
    }
