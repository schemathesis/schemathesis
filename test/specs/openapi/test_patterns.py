import re
import sys

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from schemathesis.specs.openapi.patterns import update_quantifier

SKIP_BEFORE_PY11 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Possessive repeat is only available in Python 3.11+"
)


@pytest.mark.parametrize(
    "pattern, min_length, max_length, expected",
    [
        # Single literal
        ("a", None, 3, "(a){1,3}"),
        ("a", 3, 3, "(a){3}"),
        ("a", 0, 3, "(a){1,3}"),
        # Simple quantifiers on a simple group
        (".*", None, 3, "(.){0,3}"),
        (".*", 0, 3, "(.){0,3}"),
        (".*", 1, None, "(.){1,}"),
        (".*", 1, 3, "(.){1,3}"),
        (".+", None, 3, "(.){1,3}"),
        (".+", 1, None, "(.){1,}"),
        (".+", 1, 3, "(.){1,3}"),
        (".+", 0, 3, "(.){1,3}"),
        (".?", 0, 3, "(.){0,1}"),
        (".*?", 0, 3, "(.){0,3}"),
        (".+?", 0, 3, "(.){1,3}"),
        # Complex quantifiers on a simple group
        (".{1,5}", None, 3, "(.){1,3}"),
        (".{0,3}", 1, None, "(.){1,3}"),
        (".{2,}", 1, 3, "(.){2,3}"),
        (".{1,5}?", None, 3, "(.){1,3}"),
        (".{0,3}?", 1, None, "(.){1,3}"),
        (".{2,}?", 1, 3, "(.){2,3}"),
        pytest.param(".{1,5}+", None, 3, "(.){1,3}", marks=SKIP_BEFORE_PY11),
        pytest.param(".{0,3}+", 1, None, "(.){1,3}", marks=SKIP_BEFORE_PY11),
        pytest.param(".{2,}+", 1, 3, "(.){2,3}", marks=SKIP_BEFORE_PY11),
        # Group without quantifier
        ("[a-z]", None, 5, "([a-z]){1,5}"),
        ("[a-z]", 3, None, "([a-z]){3,}"),
        ("[a-z]", 3, 5, "([a-z]){3,5}"),
        ("[a-z]", 1, 5, "([a-z]){1,5}"),
        ("a|b", 1, 5, "(a|b){1,5}"),
        # A more complex group with `*` quantifier
        ("[a-z]*", None, 5, "([a-z]){0,5}"),
        ("[a-z]*", 3, None, "([a-z]){3,}"),
        ("[a-z]*", 3, 5, "([a-z]){3,5}"),
        ("[a-z]*", 1, 5, "([a-z]){1,5}"),
        # With anchors
        ("^[a-z]*", None, 5, "^([a-z]){0,5}"),
        ("^[a-z]*", 3, 5, "^([a-z]){3,5}"),
        ("^[a-z]+", 0, 5, "^([a-z]){1,5}"),
        ("^[a-z]*$", None, 5, "^([a-z]){0,5}$"),
        ("^[a-z]*$", 3, 5, "^([a-z]){3,5}$"),
        ("^[a-z]+$", 0, 5, "^([a-z]){1,5}$"),
        ("[a-z]*$", None, 5, "([a-z]){0,5}$"),
        ("[a-z]*$", 3, 5, "([a-z]){3,5}$"),
        ("[a-z]+$", 0, 5, "([a-z]){1,5}$"),
        (r"\d*", 1, None, r"(\d){1,}"),
        # Noop
        ("abc*def*", 1, 3, "abc*def*"),
        ("[bc]*[de]*", 1, 3, "[bc]*[de]*"),
        ("[bc]3", 1, 3, "[bc]3"),
        ("b{30,35}", 1, 3, "b{30,35}"),
        ("b{1,3}", 10, None, "b{1,3}"),
        ("b", 0, 0, "b"),
        ("b$", None, None, "b$"),
        ("b$", 0, None, "b$"),
    ],
)
def test_update_quantifier(pattern, min_length, max_length, expected):
    assert update_quantifier(pattern, min_length, max_length) == expected


def test_update_quantifier_invalid_pattern():
    assert update_quantifier("*", 1, 3) == "*"


@given(st.data())
@settings(suppress_health_check=list(HealthCheck))
def test_update_quantifier_random(data):
    # Generate a regex pattern
    pattern = data.draw(st.text(min_size=1).filter(is_valid_regex))

    # Generate optional length constraints
    min_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())
    max_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())

    # Ensure min_length <= max_length if both are present
    assume(
        max_length is None
        or min_length is None
        or min_length <= max_length
        and not (min_length is None and max_length is None)
    )

    # Apply length constraints
    modified_pattern = update_quantifier(pattern, min_length, max_length)

    assume(pattern != modified_pattern)

    # Ensure the modified pattern is a valid regex
    assert is_valid_regex(modified_pattern)

    # Generate a string matching the modified pattern
    generated = data.draw(st.from_regex(modified_pattern, fullmatch=True))

    # Assert that the generated string meets the length constraints
    if min_length is not None:
        assert (
            len(generated) >= min_length
        ), f"Generated string '{generated}' is shorter than min_length {min_length}\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
    if max_length is not None:
        assert (
            len(generated) <= max_length
        ), f"Generated string '{generated}' is longer than max_length {max_length}.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
    assert re.search(
        pattern, generated
    ), f"Generated string '{generated}' does not match the pattern.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"


def is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False
