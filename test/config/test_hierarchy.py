import hypothesis
import pytest
from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

from schemathesis.config import SchemathesisConfig
from schemathesis.core import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER
from schemathesis.schemas import APIOperation, OperationDefinition

LABEL = "PUT /users/{user_id}"


@pytest.fixture
def operation(openapi_30):
    return APIOperation(
        "/users/{user_id}",
        "PUT",
        OperationDefinition({"requestBody": {"content": {"application/json": {"schema": {}}}}}),
        openapi_30,
        label=LABEL,
        base_url="http://127.0.0.1:8080/api",
        responses=openapi_30._parse_responses({}, ""),
        security=openapi_30._parse_security({}),
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


@pytest.mark.parametrize(
    "db_value, expected_type, reuse_removed",
    [
        ("none", type(None), True),
        (HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER, InMemoryExampleDatabase, False),
        ("/tmp/db", DirectoryBasedExampleDatabase, False),
    ],
)
def test_hypothesis_database_and_reuse_phase(operation, db_value, expected_type, reuse_removed):
    cfg = SchemathesisConfig.from_dict(
        {
            "generation": {
                "max-examples": 250,
                "no-shrink": False,
                "database": db_value,
            },
        }
    )
    project = cfg.projects.get_default()

    settings = project.get_hypothesis_settings()
    if expected_type is type(None):
        assert settings.database is None
    else:
        assert isinstance(settings.database, expected_type)
    assert (hypothesis.Phase.reuse not in settings.phases) is reuse_removed
    assert hypothesis.Phase.explain not in settings.phases
    assert hypothesis.Phase.shrink in settings.phases

    # operation-specific should be identical (no overrides)
    op_settings = project.get_hypothesis_settings(operation=operation)
    # __eq__ implementation is not available in older Hypothesis versions
    assert str(op_settings.database) == str(settings.database)
    assert op_settings.phases == settings.phases


def test_hypothesis_max_examples_and_no_shrink_override(operation):
    cfg = SchemathesisConfig.from_dict(
        {
            "generation": {
                "max-examples": 330,
                "no-shrink": False,
            },
            "operations": [
                {
                    "include-name": LABEL,
                    "generation": {
                        "max-examples": 42,
                        "no-shrink": True,
                    },
                }
            ],
        }
    )
    project = cfg.projects.get_default()

    global_settings = project.get_hypothesis_settings()
    assert global_settings.max_examples == 330
    assert hypothesis.Phase.shrink in global_settings.phases
    assert hypothesis.Phase.reuse in global_settings.phases

    op_settings = project.get_hypothesis_settings(operation=operation)
    assert op_settings.max_examples == 42
    assert hypothesis.Phase.shrink not in op_settings.phases
    assert hypothesis.Phase.reuse in op_settings.phases

    assert op_settings.derandomize is False
