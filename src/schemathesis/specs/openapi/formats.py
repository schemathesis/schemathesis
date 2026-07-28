from __future__ import annotations

import ipaddress
import operator
import platform
import re
import string
import uuid
from base64 import b64encode
from functools import lru_cache
from typing import TYPE_CHECKING

from schemathesis.transport.serialization import Binary

if TYPE_CHECKING:
    from collections.abc import Callable

    from hypothesis import strategies as st


IS_PYPY = platform.python_implementation() == "PyPy"
STRING_FORMATS: dict[str, st.SearchStrategy] = {}
# For some reason PyPy can't send header values with codepoints > 128, while CPython can
if IS_PYPY:
    MAX_HEADER_CODEPOINT = 128
    DEFAULT_HEADER_EXCLUDE_CHARACTERS = "\n\r\x1f\x1e\x1d\x1c"
else:
    MAX_HEADER_CODEPOINT = 255
    DEFAULT_HEADER_EXCLUDE_CHARACTERS = "\n\r"

# RFC 9110 Section 5.5: invalid field value chars are 0x00-0x08, 0x0A-0x1F, 0x7F
# Note: 0x09 (HTAB) is valid per RFC, so excluded from this set
INVALID_HEADER_CHARS = "".join(chr(i) for i in range(9)) + "".join(chr(i) for i in range(10, 32)) + "\x7f"


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    r"""Register a custom Hypothesis strategy for generating string format data.

    Args:
        name: String format name that matches the "format" keyword in your API schema
        strategy: Hypothesis strategy to generate values for this format

    Example:
        ```python
        import schemathesis
        from hypothesis import strategies as st

        # Register phone number format
        phone_strategy = st.from_regex(r"\+1-\d{3}-\d{3}-\d{4}")
        schemathesis.openapi.format("phone", phone_strategy)

        # Register email with specific domain
        email_strategy = st.from_regex(r"[a-z]+@company\.com")
        schemathesis.openapi.format("company-email", email_strategy)
        ```

    Schema usage:
        ```yaml
        properties:
          phone:
            type: string
            format: phone          # Uses your phone_strategy
          contact_email:
            type: string
            format: company-email  # Uses your email_strategy
        ```

    """
    from hypothesis.strategies import SearchStrategy

    if not isinstance(name, str):
        raise TypeError(f"name must be of type {str}, not {type(name)}")
    if not isinstance(strategy, SearchStrategy):
        raise TypeError(f"strategy must be of type {SearchStrategy}, not {type(strategy)}")

    # Wrap bytes in Binary so hypothesis-jsonschema can process binary data from user-provided formats
    def wrap_bytes(value: bytes | str | Binary) -> Binary | str:
        if isinstance(value, bytes):
            return Binary(value)
        return value

    STRING_FORMATS[name] = strategy.map(wrap_bytes)
    _invalidate_caches()


def unregister_string_format(name: str) -> None:
    """Remove format strategy from the registry."""
    try:
        del STRING_FORMATS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Open API format: {name}") from exc
    _invalidate_caches()


def _invalidate_caches() -> None:
    """Drop strategies built while the registry said something else."""
    from schemathesis.generation.hypothesis import canonical_strategy_cache, custom_formats_cache

    custom_formats_cache.clear()
    canonical_strategy_cache.clear()


def header_values(
    codec: str | None = None, exclude_characters: str = DEFAULT_HEADER_EXCLUDE_CHARACTERS
) -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    return st.text(
        alphabet=st.characters(
            min_codepoint=0, max_codepoint=MAX_HEADER_CODEPOINT, codec=codec, exclude_characters=exclude_characters
        )
        # Header values with leading non-visible chars can't be sent with `requests`
    ).map(str.lstrip)


HEADER_FORMAT = "_header_value"


def duration_values() -> st.SearchStrategy[str]:
    """Generate RFC 3339 duration values."""
    from hypothesis import strategies as st

    component = st.integers(min_value=1, max_value=10_000)
    date_part = st.one_of(
        component.map(lambda n: f"{n}D"),
        component.map(lambda n: f"{n}W"),
        st.builds(lambda years, months, days: f"{years}Y{months}M{days}D", component, component, component),
    )
    time_part = st.one_of(
        component.map(lambda n: f"{n}H"),
        component.map(lambda n: f"{n}M"),
        component.map(lambda n: f"{n}S"),
        st.builds(lambda hours, minutes, seconds: f"{hours}H{minutes}M{seconds}S", component, component, component),
    )
    date_time_part = st.builds(
        lambda days, hours, minutes, seconds: f"{days}DT{hours}H{minutes}M{seconds}S",
        component,
        component,
        component,
        component,
    )
    return st.one_of(
        date_part.map(lambda part: f"P{part}"),
        time_part.map(lambda part: f"PT{part}"),
        date_time_part.map(lambda part: f"P{part}"),
    )


# The generators below cover the JSON Schema `format` vocabulary, written from the grammars the
# formats name. `hypothesis-jsonschema` covers the same vocabulary and was the reference for which
# names need a generator at all.
# Lower case only: host names are case-insensitive, so case variety exercises nothing, and a
# 62-character alphabet costs ~1.7x more to draw from across a `format`-heavy schema.
_LABEL_CHARACTERS = string.ascii_lowercase + string.digits
_TOP_LEVEL_DOMAINS = ("com", "org", "net", "io", "dev", "info", "example", "test")
# RFC 5322 dot-atom: every character an unquoted local part may carry.
# The punctuation is what trips naive parsers, so it earns its place; case does not.
_LOCAL_PART_CHARACTERS = string.ascii_lowercase + string.digits + "!#$%&'*+-/=?^_`{|}~"


def domain_values() -> st.SearchStrategy[str]:
    """Generate domain names."""
    from hypothesis import strategies as st

    # Assembled from an alphabet rather than drawn from `hypothesis.provisional.domains`, which
    # costs ~1.3ms per value and emits the mixed-case ACE prefix validators reject.
    # One flat draw per label. Building labels out of hyphen-joined chunks nests two more list
    # draws under every domain, which costs ~2.5x across a `format`-heavy schema.
    label = st.text(alphabet=_LABEL_CHARACTERS, min_size=1, max_size=8)
    return st.builds(
        lambda labels, top_level: ".".join([*labels, top_level]),
        st.lists(label, max_size=2),
        st.sampled_from(_TOP_LEVEL_DOMAINS),
    )


def email_values() -> st.SearchStrategy[str]:
    """Generate email addresses."""
    from hypothesis import strategies as st

    # A single dot-atom: `.` is absent from the alphabet, so no leading, trailing or doubled dot
    # can appear and no second draw is needed to place one.
    local = st.text(alphabet=_LOCAL_PART_CHARACTERS, min_size=1, max_size=10)
    return st.builds("{}@{}".format, local, domain_values())


# RFC 3339, Section 5.6 — each production of the grammar is its own JSON Schema format name.
RFC3339_FORMATS = (
    "date-fullyear",
    "date-month",
    "date-mday",
    "time-hour",
    "time-minute",
    "time-second",
    "time-secfrac",
    "time-numoffset",
    "time-offset",
    "partial-time",
    "full-date",
    "full-time",
    "date-time",
)


@lru_cache
def _rfc3339_strategies() -> dict[str, st.SearchStrategy[str]]:
    """One strategy per production of the RFC 3339, Section 5.6 grammar."""
    from hypothesis import strategies as st

    def number(low: int, high: int, width: int) -> st.SearchStrategy[str]:
        return st.integers(low, high).map(lambda value: f"{value:0{width}d}")

    hour = number(0, 23, 2)
    minute = number(0, 59, 2)
    # `60` is only admitted at a leap second, which the surrounding date decides.
    second = number(0, 59, 2)
    secfrac = st.text(alphabet=string.digits, min_size=1, max_size=6).map(".".__add__)
    numoffset = st.builds("{}{}:{}".format, st.sampled_from("+-"), hour, minute)
    offset = st.just("Z") | numoffset
    partial_time = st.builds("{}:{}:{}{}".format, hour, minute, second, st.just("") | secfrac)
    # `date` renders as the `full-date` grammar and keeps the day within the month.
    full_date = st.dates().map(str)
    full_time = st.builds(operator.add, partial_time, offset)
    return {
        "date-fullyear": number(0, 9999, 4),
        "date-month": number(1, 12, 2),
        # The upper bound varies by month, and this production carries no month to consult.
        "date-mday": number(1, 28, 2),
        "time-hour": hour,
        "time-minute": minute,
        "time-second": second,
        "time-secfrac": secfrac,
        "time-numoffset": numoffset,
        "time-offset": offset,
        "partial-time": partial_time,
        "full-date": full_date,
        "full-time": full_time,
        "date-time": st.builds("{}T{}".format, full_date, full_time),
    }


def rfc3339_values(name: str) -> st.SearchStrategy[str]:
    """Generate one RFC 3339 production."""
    return _rfc3339_strategies()[name]


# The names `webcolors` accepts, which is what validators check `color` against.
_CSS21_COLOR_NAMES = (
    "aqua",
    "black",
    "blue",
    "fuchsia",
    "green",
    "gray",
    "lime",
    "maroon",
    "navy",
    "olive",
    "orange",
    "purple",
    "red",
    "silver",
    "teal",
    "white",
    "yellow",
)


def color_values() -> st.SearchStrategy[str]:
    """Generate CSS color values."""
    from hypothesis import strategies as st

    # One integer draw per value; the equivalent `#([a-fA-F0-9]{3}|[a-fA-F0-9]{6})` regex costs
    # roughly six times as much. Hex digits are case-insensitive, so both cases are admitted.
    def render(value: int, width: int, upper: bool) -> str:
        return f"#{value:0{width}{'X' if upper else 'x'}}"

    short = st.builds(render, st.integers(0, 0xFFF), st.just(3), st.booleans())
    long = st.builds(render, st.integers(0, 0xFFFFFF), st.just(6), st.booleans())
    return short | long | st.sampled_from(_CSS21_COLOR_NAMES)


# Sampled rather than drawn from `st.from_regex`, which dominates the cost of building a pattern.
# Anchors and backreferences are absent: they are only valid in positions a quantifier cannot follow.
_REGEX_ATOMS = (".", *(f"\\{character}" for character in "dDsSwW"))
# Counts stay short and every quantifier is optional. `{99999999999}` overflows the regex compiler
# rather than failing as a syntax error, and no useful pattern needs counts that large.
_REGEX_QUANTIFIERS = ("", "+", "*", "?", "+?", "*?", "??", "{0}", "{1}", "{2}", "{1,}", "{1,3}", "{2,4}", "{1,3}?")
_REGEX_METACHARACTERS = "\\^$.[]|()?*+{}"


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except (re.error, FutureWarning, OverflowError, RecursionError):
        return False
    return True


def regex_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Generate regular expressions."""
    from hypothesis import strategies as st

    # Every piece is an atom followed by an optional quantifier, so patterns compile by
    # construction. Drawing fragments independently instead leaves the majority uncompilable, and
    # discarding those costs more than everything else in this generator combined.
    literal = alphabet.filter(lambda character: character not in _REGEX_METACHARACTERS)
    atom = st.sampled_from(_REGEX_ATOMS) | st.text(alphabet=literal, min_size=1, max_size=3)
    piece = st.builds(operator.add, atom, st.sampled_from(_REGEX_QUANTIFIERS))
    return st.builds(
        "{}{}{}".format,
        st.sampled_from(("", "^")),
        st.lists(piece, max_size=5).map("".join),
        st.sampled_from(("", "$")),
        # Sound net rather than the workhorse: nothing above is expected to fail compilation.
    ).filter(_compiles)


def json_pointer_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Generate JSON pointers over the character set in force.

    RFC 6901, Section 3: a pointer is a run of `/`-prefixed tokens, and a token spells `~` as `~0`
    and `/` as `~1`. `~` goes first, or the `/` it introduces would be escaped twice.
    """
    from hypothesis import strategies as st

    # Bounded: `format` values are length-filtered downstream, and unbounded tokens push most
    # draws past the bound, where they are discarded.
    token = st.text(alphabet, max_size=8).map(lambda part: "/" + part.replace("~", "~0").replace("/", "~1"))
    return st.lists(token, max_size=3).map("".join)


def relative_json_pointer_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Generate relative JSON pointers over the character set in force.

    A non-negative integer of levels to ascend, then either `#` for the key at that level or a
    JSON pointer down from it.
    """
    from hypothesis import strategies as st

    return st.builds(
        operator.add,
        st.integers(min_value=0).map(str),
        st.just("#") | json_pointer_values(alphabet),
    )


def get_alphabet_format_strategies() -> dict[str, Callable[[st.SearchStrategy[str]], st.SearchStrategy[str]]]:
    """Formats whose values are built from the character set in force rather than fixed up front."""
    return {
        "regex": regex_values,
        "json-pointer": json_pointer_values,
        "relative-json-pointer": relative_json_pointer_values,
    }


@lru_cache
def get_default_format_strategies() -> dict[str, st.SearchStrategy]:
    """Get all default "format" strategies."""
    from hypothesis import strategies as st
    from requests.auth import _basic_auth_str

    latin1_text = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=255))

    # Exclude RFC 9110 invalid chars so generated header values always pass `is_valid_header`
    header_value = header_values(exclude_characters=INVALID_HEADER_CHARS)

    email_strategy = email_values()

    domains = domain_values()

    return {
        **{name: rfc3339_values(name) for name in RFC3339_FORMATS},
        "date": rfc3339_values("full-date"),
        "time": rfc3339_values("full-time"),
        "color": color_values(),
        "hostname": domains,
        "idn-hostname": domains,
        "ipv4": st.integers(0, 2**32 - 1).map(lambda packed: str(ipaddress.IPv4Address(packed))),
        "ipv6": st.integers(0, 2**128 - 1).map(lambda packed: str(ipaddress.IPv6Address(packed))),
        **{name: domains.map("https://{}".format) for name in ("uri", "uri-reference", "iri", "iri-reference")},
        "uri-template": domains.map("https://{}/{{id}}".format),
        "email": email_strategy,
        "idn-email": email_strategy,
        "binary": st.binary().map(Binary),
        "byte": st.binary().map(lambda x: b64encode(x).decode()),
        "duration": duration_values(),
        # `st.uuids` supplies the entropy; drawing the integer directly biases hard toward zero and
        # makes `00000000-0000-1000-8000-000000000000` the value examples show.
        "uuid": st.builds(
            lambda drawn, version: str(uuid.UUID(int=drawn.int, version=version)),
            st.uuids(),
            st.sampled_from([1, 2, 3, 4, 5]),
        ),
        # RFC 7230, Section 3.2.6
        "_header_name": st.text(
            min_size=1, alphabet=st.sampled_from("!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters)
        ),
        HEADER_FORMAT: header_value,
        "_basic_auth": st.tuples(latin1_text, latin1_text).map(lambda item: _basic_auth_str(*item)),
        "_bearer_auth": header_value.map("Bearer {}".format),
    }
