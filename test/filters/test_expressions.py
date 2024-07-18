import pytest

from schemathesis.filters import parse_expression


@pytest.mark.parametrize(
    "expression, expected",
    [
        ('/user/name == "John Doe"', ("/user/name", "==", "John Doe")),
        ("/user/age != 30", ("/user/age", "!=", 30)),
        ("/user/active == true", ("/user/active", "==", True)),
        ("/user/middleName == null", ("/user/middleName", "==", None)),
        ("/user/score == 95.5", ("/user/score", "==", 95.5)),
        ('/user/tags == ["admin", "user"]', ("/user/tags", "==", ["admin", "user"])),
        (
            '/user/address == {"city": "New York", "zip": "10001"}',
            ("/user/address", "==", {"city": "New York", "zip": "10001"}),
        ),
        ('/users/0/friends/1/name == "Alice"', ("/users/0/friends/1/name", "==", "Alice")),
        ('  /user/name  ==  "John Doe"  ', ("/user/name", "==", "John Doe")),
        ("/user/age!=30", ("/user/age", "!=", 30)),
        ('/user/full_name == "John Doe Smith"', ("/user/full_name", "==", "John Doe Smith")),
        ('/user/email == "user@example.com"', ("/user/email", "==", "user@example.com")),
        ('/user/middleName == ""', ("/user/middleName", "==", "")),
        ("/user/data == {invalid:json}", ("/user/data", "==", "{invalid:json}")),
    ],
)
def test_valid_expressions(expression, expected):
    assert parse_expression(expression) == expected


@pytest.mark.parametrize(
    "invalid_expression",
    [
        '/user/name "John Doe"',
        '/user/name <> "John Doe"',
        "",
        "   ",
        "/user/name ==",
        '== "John Doe"',
    ],
)
def test_invalid_expressions(invalid_expression):
    with pytest.raises(ValueError):
        parse_expression(invalid_expression)
