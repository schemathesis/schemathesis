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

from schemathesis.config import GenerationConfig
from schemathesis.core import MAX_STRING_LENGTH
from schemathesis.core.file_samples import FILE_SAMPLES
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.validation import has_surrounding_whitespace
from schemathesis.generation.jsonschema.context import Alphabet, FormatLengths
from schemathesis.transport.serialization import Binary

if TYPE_CHECKING:
    from collections.abc import Callable

    from hypothesis import strategies as st


IS_PYPY = platform.python_implementation() == "PyPy"
STRING_FORMATS: dict[str, st.SearchStrategy] = {}
# PyPy cannot send header values with codepoints above 128
if IS_PYPY:
    MAX_HEADER_CODEPOINT = 128
    DEFAULT_HEADER_EXCLUDE_CHARACTERS = "\n\r\x1f\x1e\x1d\x1c"
else:
    MAX_HEADER_CODEPOINT = 255
    DEFAULT_HEADER_EXCLUDE_CHARACTERS = "\n\r"

# RFC 9110 Section 5.5 forbids 0x00-0x08, 0x0A-0x1F and 0x7F in field values; HTAB (0x09) is valid
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

    # Wrap bytes in `Binary` so a user-provided format can carry binary data through generation
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
        # `requests` refuses leading whitespace and servers strip trailing; filtering keeps the drawn length.
    ).filter(lambda value: not has_surrounding_whitespace(value))


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


# Lower case only: host names are case-insensitive, so mixed case exercises nothing.
_LABEL_CHARACTERS = string.ascii_lowercase + string.digits
_TOP_LEVEL_DOMAINS = ("com", "org", "net", "io", "dev", "info", "example", "test")
# RFC 5322 dot-atom characters; the punctuation trips naive parsers, case does not.
_LOCAL_PART_CHARACTERS = string.ascii_lowercase + string.digits + "!#$%&'*+-/=?^_`{|}~"


def domain_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    # One flat draw per label: `provisional.domains` is slow and emits the mixed-case ACE prefix validators reject.
    label = st.text(alphabet=_LABEL_CHARACTERS, min_size=1, max_size=8)
    # At least two labels: a dotless domain is well-formed, yet stricter checkers reject it.
    return st.builds(
        lambda labels, top_level: ".".join([*labels, top_level]),
        st.lists(label, min_size=1, max_size=2),
        st.sampled_from(_TOP_LEVEL_DOMAINS),
    )


def email_values() -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    # The alphabet lacks `.`, so no leading, trailing or doubled dot can appear.
    local = st.text(alphabet=_LOCAL_PART_CHARACTERS, min_size=1, max_size=10)
    return st.builds("{}@{}".format, local, domain_values())


# RFC 3339, Section 5.6: each grammar production is its own JSON Schema format name.
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

    # One integer draw per value, cheaper than a regex; hex digits are case-insensitive, so both cases appear.
    def render(value: int, width: int, upper: bool) -> str:
        return f"#{value:0{width}{'X' if upper else 'x'}}"

    short = st.builds(render, st.integers(0, 0xFFF), st.just(3), st.booleans())
    long = st.builds(render, st.integers(0, 0xFFFFFF), st.just(6), st.booleans())
    return short | long | st.sampled_from(_CSS21_COLOR_NAMES)


# Sampled, as `st.from_regex` is costly; no anchors or backreferences, since a quantifier cannot follow them.
_REGEX_ATOMS = (".", *(f"\\{character}" for character in "dDsSwW"))
# Short, optional counts: `{99999999999}` overflows the regex compiler instead of raising a syntax error.
_REGEX_QUANTIFIERS = ("", "+", "*", "?", "+?", "*?", "??", "{0}", "{1}", "{2}", "{1,}", "{1,3}", "{2,4}", "{1,3}?")
_REGEX_METACHARACTERS = "\\^$.[]|()?*+{}"


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except (re.error, FutureWarning, OverflowError, RecursionError):
        return False
    return True


def regex_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    # An atom plus an optional quantifier compiles by construction; independent fragments mostly would not.
    literal = alphabet.filter(lambda character: character not in _REGEX_METACHARACTERS)
    atom = st.sampled_from(_REGEX_ATOMS) | st.text(alphabet=literal, min_size=1, max_size=3)
    piece = st.builds(operator.add, atom, st.sampled_from(_REGEX_QUANTIFIERS))
    return st.builds(
        "{}{}{}".format,
        st.sampled_from(("", "^")),
        st.lists(piece, max_size=5).map("".join),
        st.sampled_from(("", "$")),
        # A safety net; the construction above should always compile.
    ).filter(_compiles)


def json_pointer_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """JSON pointers over the character set in force; tokens escape `~` before `/`, or `~1` would become `~01`."""
    from hypothesis import strategies as st

    # Bounded: downstream length filters would discard most draws of unbounded tokens.
    token = st.text(alphabet, max_size=8).map(lambda part: "/" + part.replace("~", "~0").replace("/", "~1"))
    return st.lists(token, max_size=3).map("".join)


def relative_json_pointer_values(alphabet: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Relative JSON pointers: levels to ascend, then `#` for the key there or a JSON pointer down from it."""
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


# Length range of each built-in generator; callers skip the search for a length window outside it.
FORMAT_LENGTHS: dict[str, tuple[int, int]] = {
    "date": (10, 10),
    "full-date": (10, 10),
    "date-fullyear": (4, 4),
    "date-month": (2, 2),
    "date-mday": (2, 2),
    "time-hour": (2, 2),
    "time-minute": (2, 2),
    "time-second": (2, 2),
    "time-secfrac": (2, 7),
    "time-numoffset": (6, 6),
    "time-offset": (1, 6),
    "partial-time": (8, 15),
    "full-time": (9, 21),
    "time": (9, 21),
    "date-time": (20, 32),
    "duration": (3, 26),
    "hostname": (4, 25),
    "idn-hostname": (4, 25),
    "ipv4": (7, 15),
    "ipv6": (2, 39),
    "uri": (12, 33),
    "uri-reference": (12, 33),
    "iri": (12, 33),
    "iri-reference": (12, 33),
    "uri-template": (17, 38),
    "email": (6, 36),
    "idn-email": (6, 36),
    "uuid": (36, 36),
    # Counted in bytes, not characters.
    "binary": (0, MAX_STRING_LENGTH),
}


def format_length_bounds(name: str, strategy: st.SearchStrategy | None) -> tuple[int, int] | None:
    """Lengths `strategy` is held to, or `None` when it is not the built-in one or can be pointed elsewhere."""
    if get_default_format_strategies().get(name) is not strategy or name in FORMAT_NARROWERS:
        return None
    return FORMAT_LENGTHS.get(name)


# "YYYY-MM-DDThh:mm:ss", before the fractional second and the offset.
_DATE_TIME_BASE_LENGTH = 19


def date_time_values_within(min_length: int, max_length: float) -> st.SearchStrategy[str] | None:
    """`date-time` values whose length lands inside the window, or `None` when no shape fits it."""
    from hypothesis import strategies as st

    date = rfc3339_values("full-date")
    hour = rfc3339_values("time-hour")
    minute = rfc3339_values("time-minute")
    second = rfc3339_values("time-second")
    numoffset = rfc3339_values("time-numoffset")
    branches = []
    for digits in range(7):
        fraction = (
            st.just("")
            if digits == 0
            else st.text(alphabet=string.digits, min_size=digits, max_size=digits).map(".".__add__)
        )
        fraction_length = digits + 1 if digits else 0
        for offset, offset_length in ((st.just("Z"), 1), (numoffset, 6)):
            if not min_length <= _DATE_TIME_BASE_LENGTH + fraction_length + offset_length <= max_length:
                continue
            branches.append(st.builds("{}T{}:{}:{}{}{}".format, date, hour, minute, second, fraction, offset))
    return st.one_of(branches) if branches else None


# RFC 1035, Section 2.3.4 - one label of a domain name.
_MAX_DOMAIN_LABEL_LENGTH = 63
# The shortest domain a dot fits in, as "a.b".
_MIN_DOMAIN_LENGTH = 3
# The shortest and longest address the format checkers accept: "a@b.c" up to RFC 5321's path limit.
_EMAIL_LENGTHS = (_MIN_DOMAIN_LENGTH + 2, 255)
_URI_SCHEME = "https://"
# "https://" plus the shortest domain, up to the longest string this engine builds at all.
_URI_LENGTHS = (len(_URI_SCHEME) + _MIN_DOMAIN_LENGTH, MAX_STRING_LENGTH)


def _domain_label_lengths(total: int) -> list[int]:
    """Label lengths that render as a `total`-character domain, each inside the label cap."""
    lengths = []
    remaining = total
    while remaining > _MAX_DOMAIN_LABEL_LENGTH:
        # Leave room for one more label and the dot before it.
        lengths.append(min(_MAX_DOMAIN_LABEL_LENGTH, remaining - 2))
        remaining -= lengths[-1] + 1
    lengths.append(remaining)
    # A dotless domain is well-formed, yet the stricter checkers real services run reject it.
    if len(lengths) == 1 and lengths[0] >= 3:
        lengths = [lengths[0] - 2, 1]
    return lengths


def _domain_of_length(total: int) -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    lengths = _domain_label_lengths(total)
    head = st.text(alphabet=_LABEL_CHARACTERS, min_size=lengths[0], max_size=lengths[0])
    if len(lengths) == 1:
        return head
    # Only the first label is drawn: a long domain would outgrow the generation buffer, and only its length matters.
    filler = ".".join("a" * length for length in lengths[1:])
    return head.map(lambda label: f"{label}.{filler}")


def _length_targets(name: str, min_length: int, max_length: float, renderable: tuple[int, int]) -> list[int]:
    """The lengths to build for, at the ends of what the window and the format share."""
    shortest, longest = renderable
    bottom = max(min_length, shortest)
    # Building longer than the plain generator already reaches only pays where the floor leaves no choice.
    drawn_longest = FORMAT_LENGTHS[name][1]
    top = min(max_length, longest if bottom > drawn_longest else drawn_longest)
    if bottom > top:
        return []
    return sorted({bottom, int(top)})


def email_values_within(min_length: int, max_length: float) -> st.SearchStrategy[str] | None:
    """Email addresses whose length lands inside the window, or `None` when none can."""
    from hypothesis import strategies as st

    # The domain carries the length: a local part is capped at 64 characters, a domain is not.
    branches = [
        st.builds(
            "{}@{}".format,
            st.text(alphabet=_LOCAL_PART_CHARACTERS, min_size=1, max_size=1),
            _domain_of_length(target - 2),
        )
        for target in _length_targets("email", min_length, max_length, _EMAIL_LENGTHS)
    ]
    return st.one_of(branches) if branches else None


def uri_values_within(min_length: int, max_length: float) -> st.SearchStrategy[str] | None:
    """URIs whose length lands inside the window, or `None` when none can."""
    from hypothesis import strategies as st

    branches = [
        _domain_of_length(target - len(_URI_SCHEME)).map(_URI_SCHEME.__add__)
        for target in _length_targets("uri", min_length, max_length, _URI_LENGTHS)
    ]
    return st.one_of(branches) if branches else None


def binary_values_within(min_length: int, max_length: float) -> st.SearchStrategy[str]:
    """Binary payloads whose byte count lands inside the window."""
    from hypothesis import strategies as st

    max_size = None if max_length == float("inf") else int(max_length)
    samples = [sample for sample in FILE_SAMPLES.values() if min_length <= len(sample) <= max_length]
    arbitrary = st.binary(min_size=min_length, max_size=max_size)
    if samples:
        return st.one_of(st.sampled_from(samples), arbitrary).map(Binary)
    return arbitrary.map(Binary)


# Generators that can be pointed at a narrower window instead of drawn from and filtered.
FORMAT_NARROWERS: dict[str, Callable[[int, float], st.SearchStrategy[str] | None]] = {
    "date-time": date_time_values_within,
    "email": email_values_within,
    "idn-email": email_values_within,
    "uri": uri_values_within,
    "uri-reference": uri_values_within,
    "iri": uri_values_within,
    "iri-reference": uri_values_within,
    "binary": binary_values_within,
}


def format_lengths_for(formats: dict[str, st.SearchStrategy]) -> dict[str, FormatLengths]:
    """What is known about the lengths in `formats`, skipping every name with a caller-supplied generator."""
    defaults = get_default_format_strategies()
    return _format_lengths(frozenset(name for name in FORMAT_LENGTHS if formats.get(name) is defaults.get(name)))


@lru_cache
def _format_lengths(names: frozenset[str]) -> dict[str, FormatLengths]:
    # Shared between callers, which only read it.
    return {
        name: FormatLengths(shortest, longest, FORMAT_NARROWERS.get(name))
        for name, (shortest, longest) in FORMAT_LENGTHS.items()
        if name in names
    }


@lru_cache
def get_default_format_strategies() -> dict[str, st.SearchStrategy]:
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
        # Real files come first, so a single draw yields one that upload handlers can parse.
        "binary": st.one_of(st.sampled_from(list(FILE_SAMPLES.values())), st.binary()).map(Binary),
        "byte": st.binary().map(lambda x: b64encode(x).decode()),
        "duration": duration_values(),
        # `st.uuids` supplies the entropy; a drawn integer biases toward zero, so examples would show all zeros.
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


def header_alphabet(generation_config: GenerationConfig) -> Alphabet:
    """Characters and shape a header name or value may carry, under the caller's generation settings."""
    excluded = generation_config.exclude_header_characters
    if excluded is None:
        excluded = DEFAULT_HEADER_EXCLUDE_CHARACTERS
    # A codec the user asked for wins; the default one is widened to everything a header may carry.
    codec = generation_config.codec if generation_config.codec not in (None, "utf-8") else "ascii"
    return Alphabet(
        allow_x00=generation_config.allow_x00,
        codec=codec,
        max_codepoint=MAX_HEADER_CODEPOINT,
        exclude_characters="".join(sorted(set(excluded + INVALID_HEADER_CHARS))),
        allow_surrounding_whitespace=False,
        min_name_length=1,
        name_characters="!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters,
    )


# RFC 6265: printable ASCII except space, DQUOTE, comma, semicolon and backslash; servers split or strip the rest.
COOKIE_EXCLUDE_CHARACTERS = "".join(chr(code) for code in range(0x21)) + '",;\\'


def cookie_alphabet(generation_config: GenerationConfig) -> Alphabet:
    """Characters a cookie name or value may carry, under the caller's generation settings."""
    excluded = generation_config.exclude_header_characters or ""
    return Alphabet(
        allow_x00=False,
        codec="ascii",
        max_codepoint=0x7E,
        exclude_characters="".join(sorted(set(excluded + COOKIE_EXCLUDE_CHARACTERS))),
        min_name_length=1,
        name_characters="!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters,
    )


def alphabet_for(location: ParameterLocation | None, generation_config: GenerationConfig) -> Alphabet:
    """Characters a value sent in `location` may carry, under the caller's generation settings."""
    if location == ParameterLocation.COOKIE:
        return cookie_alphabet(generation_config)
    if location == ParameterLocation.HEADER:
        return header_alphabet(generation_config)
    return Alphabet(allow_x00=generation_config.allow_x00, codec=generation_config.codec)
