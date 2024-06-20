import pytest

from schemathesis.specs.openapi.checks import ResourcePath, _is_prefix_operation


@pytest.mark.parametrize(
    "lhs, lhs_vars, rhs, rhs_vars, expected",
    [
        # Exact match, no variables
        ("/users/123", {}, "/users/123", {}, True),
        # Different paths, no variables
        ("/users/123", {}, "/users/456", {}, False),
        # Different variable names
        ("/users/{id}", {"id": "123"}, "/users/{user_id}", {"user_id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{user_id}", {"user_id": "456"}, False),
        # Singular vs. plural
        ("/user/{id}", {"id": "123"}, "/users/{id}", {"id": "123"}, True),
        ("/user/{id}", {"id": "123"}, "/users/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/user/{id}", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/user/{id}", {"id": "456"}, False),
        # Trailing slashes
        ("/users/{id}/", {"id": "123"}, "/users/{id}", {"id": "123"}, True),
        ("/users/{id}/", {"id": "123"}, "/users/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{id}/", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/", {"id": "456"}, False),
        ("/users/", {}, "/users", {}, True),
        ("/users", {}, "/users/", {}, True),
        # Empty paths
        ("", {}, "", {}, True),
        ("", {}, "/", {}, True),
        ("/", {}, "", {}, True),
        # Mismatched paths
        ("/users/{id}", {"id": "123"}, "/products/{id}", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{name}", {"name": "John"}, False),
        # LHS is a prefix of RHS
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
        # LHS is a prefix of RHS, with different number of variables
        ("/users/{id}", {"id": "123"}, "/users/{id}/{name}", {"id": "123", "name": "John"}, True),
        (
            "/users/{id}",
            {"id": "123"},
            "/users/{id}/{name}/{email}",
            {"id": "123", "name": "John", "email": "john@example.com"},
            True,
        ),
        # LHS is a prefix of RHS, with different variable values
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
        # LHS is a prefix of RHS, with different variable types
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": 123}, True),
        ("/users/{id}", {"id": 123}, "/users/{id}/details", {"id": "123"}, True),
        # LHS is a prefix of RHS, with extra path segments
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view", {"id": "456"}, False),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details/view/edit", {"id": "456"}, False),
        # LHS is a prefix of RHS
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "123"}, True),
        ("/users/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
        # Longer than a prefix
        ("/one/two/three/four/{id}", {"id": "123"}, "/users/{id}/details", {"id": "456"}, False),
    ],
)
def test_is_prefix_operation(lhs, lhs_vars, rhs, rhs_vars, expected):
    assert _is_prefix_operation(ResourcePath(lhs, lhs_vars), ResourcePath(rhs, rhs_vars)) == expected
