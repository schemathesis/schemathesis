import datetime
import re
import uuid

import jsonschema_rs
import pytest

from schemathesis.specs.openapi.negative.value_channel import (
    _NEGATIVE_MAX_LENGTH_CAP,
    violate_date,
    violate_date_time,
    violate_email,
    violate_enum,
    violate_max_length,
    violate_maximum,
    violate_min_length,
    violate_minimum,
    violate_multiple_of,
    violate_pattern,
    violate_uuid,
)


def test_violate_uuid_keeps_shape_breaks_validity():
    violated = violate_uuid("12345678-ce2b-2f6f-a0cd-faaa313ba470")
    assert len(violated) == 36
    assert violated.count("-") == 4
    with pytest.raises(ValueError):
        uuid.UUID(violated)


def test_violate_email_breaks_validation():
    validator = jsonschema_rs.validator_for({"type": "string", "format": "email"}, validate_formats=True)
    assert not validator.is_valid(violate_email("user@example.com"))


def test_violate_date_time_breaks_validation():
    violated = violate_date_time("2024-01-01T12:30:00Z")
    with pytest.raises(ValueError):
        datetime.datetime.fromisoformat(violated)


def test_violate_date_breaks_validation():
    with pytest.raises(ValueError):
        datetime.date.fromisoformat(violate_date("2024-01-01"))


def test_violate_pattern_breaks_match():
    assert not re.match("^[a-z]+$", violate_pattern("abc", "^[a-z]+$"))


@pytest.mark.parametrize(("min_length", "expected"), [(4, "hel"), (1, ""), (0, "")])
def test_violate_min_length_below(min_length, expected):
    assert violate_min_length("hello", min_length=min_length) == expected


def test_violate_max_length_above():
    assert violate_max_length("hello", max_length=5) == "hello*"


def test_violate_max_length_expands_up_to_the_cap():
    assert len(violate_max_length("", max_length=_NEGATIVE_MAX_LENGTH_CAP)) == _NEGATIVE_MAX_LENGTH_CAP + 1


def test_violate_minimum_below():
    assert violate_minimum(5, minimum=5) < 5


def test_violate_maximum_above():
    assert violate_maximum(5, maximum=5) > 5


def test_violate_enum_outside():
    assert violate_enum("a", enum=["a", "b", "c"]) not in ("a", "b", "c")


def test_violate_multiple_of_not_divisible():
    assert violate_multiple_of(10, multiple_of=5) % 5 != 0


def test_violate_uuid_for_non_uuid_input_still_yields_invalid_uuid():
    violated = violate_uuid("not-a-uuid")
    assert len(violated) == 36
    assert violated.count("-") == 4
    with pytest.raises(ValueError):
        uuid.UUID(violated)


def test_violate_enum_returns_value_not_in_enum_when_default_candidate_is_taken():
    enum = ["__not_in_enum__", "__not_in_enum___"]
    assert violate_enum("a", enum=enum) not in enum
