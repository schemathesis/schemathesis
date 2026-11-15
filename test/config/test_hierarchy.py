import hypothesis
import pytest
from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

import schemathesis
from schemathesis.config import ProjectConfig, SchemathesisConfig, WarningsConfig

LABEL = "PUT /users/{user_id}"


@pytest.fixture
def operation(ctx):
    operation = ctx.openapi.load_operation(
        "PUT /users/{user_id}",
        {
            "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "requestBody": {"content": {"application/json": {"schema": {}}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    return operation


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
    assert project.auth_for() == ("user", "global")
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

    assert project.headers_for() == {"X-Test": "global"}

    assert project.headers_for(operation=operation) == expected


def test_headers_for_none_when_unset(operation):
    config = SchemathesisConfig.from_dict({})
    project = config.projects.get_default()

    assert project.headers_for() == {}
    assert project.headers_for(operation=operation) == {}


@pytest.mark.parametrize(
    "db_value, expected_type, reuse_removed",
    [
        ("none", type(None), True),
        (":memory:", InMemoryExampleDatabase, False),
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

    op_settings = project.get_hypothesis_settings(operation=operation)
    # Older Hypothesis databases lack `__eq__`.
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


@pytest.mark.parametrize("matcher", [LABEL, "Unknown"], ids=["operation-override", "project-fallback"])
@pytest.mark.parametrize(
    ("key", "project_value", "operation_value", "getter"),
    [
        ("max-redirects", 5, 1, ProjectConfig.max_redirects_for),
        ("request-timeout", 10, 0.5, ProjectConfig.request_timeout_for),
        ("request-retries", 3, 5, ProjectConfig.request_retries_for),
        ("tls-verify", "/global/ca.pem", False, ProjectConfig.tls_verify_for),
        ("proxy", "http://global:8080", "http://local:8080", ProjectConfig.proxy_for),
        ("rate-limit", None, "auto", ProjectConfig.rate_limit_for),
    ],
    ids=["max-redirects", "request-timeout", "request-retries", "tls-verify", "proxy", "rate-limit"],
)
def test_transport_setting_for_operation(operation, matcher, key, project_value, operation_value, getter):
    raw = {"operations": [{"include-name": matcher, key: operation_value}]}
    if project_value is not None:
        raw[key] = project_value
    project = SchemathesisConfig.from_dict(raw).projects.get_default()
    expected = operation_value if matcher == LABEL else project_value
    assert (getter(project), getter(project, operation=operation)) == (project_value, expected)


@pytest.mark.parametrize(
    ("key", "operation_value", "option", "cli_value", "getter"),
    [
        ("max-redirects", 1, "max_redirects", 5, ProjectConfig.max_redirects_for),
        ("request-timeout", 0.5, "request_timeout", 10, ProjectConfig.request_timeout_for),
        ("request-retries", 5, "request_retries", 3, ProjectConfig.request_retries_for),
        ("tls-verify", False, "tls_verify", "/cli/ca.pem", ProjectConfig.tls_verify_for),
        ("proxy", "http://local:8080", "proxy", "http://cli:8080", ProjectConfig.proxy_for),
        ("rate-limit", "1/s", "rate_limit", "auto", ProjectConfig.rate_limit_for),
        ("continue-on-failure", False, "continue_on_failure", True, ProjectConfig.continue_on_failure_for),
        ("request-cert", "/local.pem", "request_cert", "/cli.pem", ProjectConfig.request_cert_for),
        ("headers", {"X-Local": "local"}, "headers", {"X-Cli": "cli"}, ProjectConfig.headers_for),
        (
            "auth",
            {"basic": {"username": "user", "password": "local"}},
            "basic_auth",
            ("user", "cli"),
            ProjectConfig.auth_for,
        ),
        ("warnings", False, "warnings", WarningsConfig.from_value(["missing_auth"]), ProjectConfig.warnings_for),
    ],
    ids=[
        "max-redirects",
        "request-timeout",
        "request-retries",
        "tls-verify",
        "proxy",
        "rate-limit",
        "continue-on-failure",
        "request-cert",
        "headers",
        "auth",
        "warnings",
    ],
)
def test_command_line_overrides_operation_setting(operation, key, operation_value, option, cli_value, getter):
    config = SchemathesisConfig.from_dict({"operations": [{"include-name": LABEL, key: operation_value}]})
    config.projects.override.update(**{option: cli_value})
    assert getter(config.projects.get_default(), operation=operation) == cli_value


def _max_examples(project, operation):
    return project.generation_for(operation=operation, phase="fuzzing").max_examples


@pytest.mark.parametrize(
    ("raw", "apply", "resolve", "expected"),
    [
        (
            {"operations": [{"include-name": LABEL, "generation": {"max-examples": 5}}]},
            lambda override: override.generation.update(max_examples=10),
            _max_examples,
            10,
        ),
        (
            {"operations": [{"include-name": LABEL, "phases": {"fuzzing": {"generation": {"max-examples": 5}}}}]},
            lambda override: override.generation.update(max_examples=10),
            _max_examples,
            10,
        ),
        (
            {"phases": {"fuzzing": {"generation": {"max-examples": 5}}}},
            lambda override: override.generation.update(max_examples=10),
            _max_examples,
            10,
        ),
        (
            {"operations": [{"include-name": LABEL, "phases": {"coverage": {"enabled": True}}}]},
            lambda override: override.phases.update(phases=["fuzzing"]),
            lambda project, operation: project.phases_for(operation=operation).coverage.enabled,
            False,
        ),
        (
            {"operations": [{"include-name": LABEL, "checks": {"max_response_time": 20}}]},
            lambda override: override.checks.update(max_response_time=5),
            lambda project, operation: (
                project.checks_config_for(operation=operation, phase="fuzzing").max_response_time.limit
            ),
            5,
        ),
    ],
    ids=[
        "operation-generation",
        "operation-phase-generation",
        "phase-generation",
        "operation-phases",
        "operation-response-time",
    ],
)
def test_command_line_overrides_operation_and_phase_sections(operation, raw, apply, resolve, expected):
    config = SchemathesisConfig.from_dict(raw)
    apply(config.projects.override)
    assert resolve(config.projects.get_default(), operation) == expected


def test_project_operations_take_precedence_over_global_operations(ctx):
    raw_schema = ctx.openapi.build_schema({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})
    config = SchemathesisConfig.from_dict(
        {
            "operations": [{"include-path": "/items", "request-timeout": 1}],
            "project": [
                {
                    "title": raw_schema["info"]["title"],
                    "operations": [{"include-path": "/items", "request-timeout": 99}],
                }
            ],
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema, config=config)
    assert schema.config.request_timeout_for(operation=schema["/items"]["GET"]) == 99


@pytest.mark.parametrize(
    ("project_settings", "operation_settings", "expected"),
    [
        ({"request-cert": "global.pem", "request-cert-key": "global.key"}, None, ("global.pem", "global.key")),
        (
            {"request-cert": "global.pem"},
            {"request-cert": "local.pem", "request-cert-key": "local.key"},
            ("local.pem", "local.key"),
        ),
        ({"request-cert": "global.pem", "request-cert-key": "global.key"}, {"request-cert": "local.pem"}, "local.pem"),
    ],
    ids=["project-cert-with-key", "operation-cert-with-key", "operation-cert-without-key"],
)
def test_request_cert_for_operation(operation, project_settings, operation_settings, expected):
    raw = dict(project_settings)
    if operation_settings is not None:
        raw["operations"] = [{"include-name": LABEL, **operation_settings}]
    project = SchemathesisConfig.from_dict(raw).projects.get_default()
    assert project.request_cert_for(operation=operation) == expected


def test_warnings_disabled_for_operation(operation):
    project = SchemathesisConfig.from_dict(
        {"operations": [{"include-name": LABEL, "warnings": False}]}
    ).projects.get_default()

    assert (repr(project.warnings_for()), repr(project.warnings_for(operation=operation))) == (
        "WarningsConfig()",
        "WarningsConfig(display=[])",
    )


def test_operation_disables_project_continue_on_failure(ctx, cli):
    args = ("--phases=fuzzing", "--max-examples=30", "--seed=1")
    stopping = ctx.openapi.apps.multiple_failures()
    cli.run(stopping.schema_url, *args)
    overridden = ctx.openapi.apps.multiple_failures()
    result = cli.run(
        overridden.schema_url,
        *args,
        config={
            "continue-on-failure": True,
            "operations": [{"include-path": "/api/multiple_failures", "continue-on-failure": False}],
        },
    )
    assert len(overridden.requests) == len(stopping.requests), result.stdout
