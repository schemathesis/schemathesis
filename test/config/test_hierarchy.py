import pytest

from schemathesis.config import SchemathesisConfig
from schemathesis.schemas import APIOperation, OperationDefinition

LABEL = "PUT /users/{user_id}"


@pytest.fixture
def operation(openapi_30):
    return APIOperation(
        "/users/{user_id}",
        "PUT",
        OperationDefinition(
            {"requestBody": {"content": {"application/json": {"schema": {}}}}},
            {"requestBody": {"content": {"application/json": {"schema": {}}}}},
            "",
        ),
        openapi_30,
        label=LABEL,
        base_url="http://127.0.0.1:8080/api",
    )


@pytest.mark.parametrize(
    ["matcher", "expected"],
    [
        (LABEL, "local"),
        ("Unknown", "global"),
    ],
)
def test_auth_for(operation, matcher, expected):
    config = SchemathesisConfig.from_dict(
        {
            "auth": {
                "basic": {"username": "user", "password": "global"},
            },
            "operations": [
                {
                    "include-name": matcher,
                    "auth": {
                        "basic": {"username": "user", "password": "local"},
                    },
                }
            ],
        }
    )
    project = config.projects.get_default()
    # No specific API operation - global auth
    assert project.auth_for() == ("user", "global")
    # Specific for operation
    assert project.auth_for(operation=operation) == ("user", expected)


@pytest.mark.parametrize(
    ["matcher", "expected"],
    [
        (LABEL, {"X-Test": "local"}),
        ("Other /path", {"X-Test": "global"}),
    ],
)
def test_headers_for_override_and_fallback(operation, matcher, expected):
    config = SchemathesisConfig.from_dict(
        {
            "headers": {"X-Test": "global"},
            "operations": [
                {
                    "include-name": matcher,
                    "headers": {"X-Test": "local"},
                }
            ],
        }
    )
    project = config.projects.get_default()

    # No operation passed -> global headers
    assert project.headers_for() == {"X-Test": "global"}

    # Operation passed -> override or fallback per matcher
    assert project.headers_for(operation=operation) == expected


def test_headers_for_none_when_unset(operation):
    # No headers defined globally or per-operation -> empty dict
    config = SchemathesisConfig.from_dict({})
    project = config.projects.get_default()

    assert project.headers_for() == {}
    assert project.headers_for(operation=operation) == {}
