from __future__ import annotations

import operator
import re
from datetime import timezone
from typing import Any

from hypothesis import assume
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

_UTC = st.just(timezone.utc)
# Single hyphens only: a `--` in a label's 3rd-4th position is reserved (RFC 5891) and rejected.
_HOSTNAME_LABEL = st.from_regex(r"[a-z](-?[a-z0-9]){0,30}", fullmatch=True)
_EMAIL_LOCAL = st.from_regex(r"[a-z0-9]{1,20}", fullmatch=True)


def _hostname(min_labels: int) -> SearchStrategy[str]:
    return st.lists(_HOSTNAME_LABEL, min_size=min_labels, max_size=4).map(".".join)


# The RFC3339 date/time atoms, json-pointer / relative-json-pointer, `regex`, and `color`
# strategies below are ported from hypothesis-jsonschema (MPL-2.0):
# https://github.com/python-jsonschema/hypothesis-jsonschema -- src/hypothesis_jsonschema/_from_schema.py


def _rfc3339(name: str) -> SearchStrategy[str]:
    def zfill(width: int) -> Any:
        return lambda value: str(value).zfill(width)

    simple = {
        "date-fullyear": st.integers(0, 9999).map(zfill(4)),
        "date-month": st.integers(1, 12).map(zfill(2)),
        "date-mday": st.integers(1, 28).map(zfill(2)),
        "time-hour": st.integers(0, 23).map(zfill(2)),
        "time-minute": st.integers(0, 59).map(zfill(2)),
        "time-second": st.integers(0, 59).map(zfill(2)),
        "time-secfrac": st.from_regex(r"\.[0-9]+"),
    }
    if name in simple:
        return simple[name]
    if name == "time-numoffset":
        return st.tuples(st.sampled_from(["+", "-"]), _rfc3339("time-hour"), _rfc3339("time-minute")).map(
            "%s%s:%s".__mod__
        )
    if name == "time-offset":
        return st.one_of(st.just("Z"), _rfc3339("time-numoffset"))
    if name == "partial-time":
        return st.times().map(str)
    if name in ("date", "full-date"):
        return st.dates().map(str)
    if name in ("time", "full-time"):
        return st.tuples(_rfc3339("partial-time"), _rfc3339("time-offset")).map("".join)
    return st.tuples(_rfc3339("full-date"), _rfc3339("full-time")).map("T".join)


_RFC3339_FORMATS = (
    "date-fullyear", "date-month", "date-mday", "time-hour", "time-minute", "time-second",
    "time-secfrac", "time-numoffset", "time-offset", "partial-time", "full-date", "full-time",
)

_JSON_POINTER_TOKEN = st.text(max_size=8).map(lambda part: "/" + part.replace("~", "~0").replace("/", "~1"))
_JSON_POINTER = st.lists(_JSON_POINTER_TOKEN, max_size=5).map("".join)
_RELATIVE_JSON_POINTER = st.builds(
    operator.add,
    st.from_regex(r"0|[1-9][0-9]*", fullmatch=True),
    st.just("#") | _JSON_POINTER,
)


@st.composite  # type: ignore[untyped-decorator]
def _regex_patterns(draw: st.DrawFn) -> str:
    fragments = st.one_of(
        st.just("."),
        st.from_regex(r"\[\^?[A-Za-z0-9]+\]", fullmatch=True),
        _REGEX_PATTERNS.map("{}+".format),
        _REGEX_PATTERNS.map("{}?".format),
        _REGEX_PATTERNS.map("{}*".format),
    )
    result = draw(st.lists(fragments, min_size=1, max_size=3).map("".join))
    try:
        re.compile(result)
    except re.error:
        assume(False)
    return result


_REGEX_PATTERNS = _regex_patterns()

_WEBCOLOR = st.from_regex("^#([a-fA-F0-9]{3}|[a-fA-F0-9]{6})$") | st.sampled_from(
    ("aqua", "black", "blue", "fuchsia", "green", "gray", "lime", "maroon", "navy",
     "olive", "orange", "purple", "red", "silver", "teal", "white", "yellow")
)


def _ecma_regex() -> SearchStrategy[str]:
    # `re.compile` (Python) is more lenient than jsonschema_rs's ECMA-262 `regex` check; filter to match it.
    import jsonschema_rs

    is_valid = jsonschema_rs.validator_for({"type": "string", "format": "regex"}, validate_formats=True).is_valid
    return _REGEX_PATTERNS.filter(is_valid)


def _builtin_formats() -> dict[str, SearchStrategy[str]]:
    date_times = st.datetimes(timezones=_UTC)
    # Domains need >= 2 labels so the validator sees a dotted host.
    host = _hostname(min_labels=2)
    uri = st.builds(lambda value: f"http://{value}", host)
    email = st.builds(lambda local, value: f"{local}@{value}", _EMAIL_LOCAL, host)
    formats: dict[str, SearchStrategy[str]] = {
        "ipv4": st.ip_addresses(v=4).map(str),
        "ipv6": st.ip_addresses(v=6).map(str),
        "date": st.dates().map(lambda value: value.isoformat()),
        "date-time": date_times.map(lambda value: value.isoformat()),
        "time": date_times.map(lambda value: value.timetz().isoformat()),
        "email": email,
        "idn-email": email,
        "hostname": _hostname(min_labels=1),
        "idn-hostname": _hostname(min_labels=1),
        "uri": uri,
        "uri-reference": uri,
        "iri": uri,
        "iri-reference": uri,
        "uri-template": uri,
        "json-pointer": _JSON_POINTER,
        "relative-json-pointer": _RELATIVE_JSON_POINTER,
        "regex": _ecma_regex(),
        "color": _WEBCOLOR,
    }
    for name in _RFC3339_FORMATS:
        formats[name] = _rfc3339(name)
    return formats


class FormatRegistry:
    """Maps a `format` name to the strategy that generates matching strings."""

    __slots__ = ("_formats",)

    def __init__(self, custom: dict[str, SearchStrategy[str]] | None = None) -> None:
        self._formats = _builtin_formats()
        if custom:
            self._formats.update(custom)

    def get(self, name: str) -> SearchStrategy[str] | None:
        return self._formats.get(name)

    def register(self, name: str, strategy: SearchStrategy[str]) -> None:
        self._formats[name] = strategy
