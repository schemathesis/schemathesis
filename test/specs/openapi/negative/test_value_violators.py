import datetime
import re
import uuid

import jsonschema_rs
import pytest

from schemathesis.specs.openapi.negative.value_channel import (
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
    violate_required,
    violate_uuid,
)


def test_violate_uuid_keeps_shape_breaks_validity():
    original = "08fadcc5-ce2b-2f6f-a0cd-faaa313ba470"
    violated = violate_uuid(original)
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
        datetime.datetime.fromisoformat(violated.replace("Z", "+00:00"))


def test_violate_date_breaks_validation():
    with pytest.raises(ValueError):
        datetime.date.fromisoformat(violate_date("2024-01-01"))


def test_violate_pattern_breaks_match():
    assert not re.match("^[a-z]+$", violate_pattern("abc", "^[a-z]+$"))


def test_violate_min_length_below():
    assert len(violate_min_length("hello", min_length=4)) < 4


def test_violate_max_length_above():
    assert len(violate_max_length("hello", max_length=5)) > 5


def test_violate_minimum_below():
    assert violate_minimum(5, minimum=5) < 5


def test_violate_maximum_above():
    assert violate_maximum(5, maximum=5) > 5


def test_violate_enum_outside():
    assert violate_enum("a", enum=["a", "b", "c"]) not in ("a", "b", "c")


def test_violate_multiple_of_not_divisible():
    assert violate_multiple_of(10, multiple_of=5) % 5 != 0


def test_violate_required_drops_field():
    body = {"a": 1, "b": 2}
    result = violate_required(body, required=["a", "b"])
    assert "a" not in result or "b" not in result


def test_violate_required_with_empty_list_returns_body_unchanged():
    body = {"a": 1}
    assert violate_required(body, required=[]) == body


def test_violate_uuid_for_non_uuid_input_still_yields_invalid_uuid():
    violated = violate_uuid("not-a-uuid")
    assert len(violated) == 36
    assert violated.count("-") == 4
    with pytest.raises(ValueError):
        uuid.UUID(violated)


def test_violate_enum_returns_value_not_in_enum_when_default_candidate_is_taken():
    enum = ["__not_in_enum__", "__not_in_enum___"]
    assert violate_enum("a", enum=enum) not in enum
