import pytest

import schemathesis
from schemathesis.config import (
    ConfigError,
    OperationConfig,
    OperationsConfig,
    ProjectConfig,
    SchemathesisConfig,
    SchemathesisWarning,
    get_workers_count,
)
from schemathesis.config._auth import WFCAuthConfig
from schemathesis.config._phases import DEFAULT_UNEXPECTED_METHODS
from schemathesis.config._validator import CONFIG_SCHEMA
from schemathesis.errors import HookError
from schemathesis.filters import FilterSet

CONFIGS = {
    "common.auth-dynamic-openapi-api-key": """
        # Valid dynamic apiKey auth (header extraction)
        [auth.dynamic.openapi.ApiKey]
        path = "/api/token"
        method = "GET"
        extract_from = "header"
        extract_selector = "X-Auth-Token"
    """,
    "common.auth-dynamic-openapi-bearer": """
        # Valid dynamic bearer auth
        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_selector = "/access_token"
    """,
    "common.auth-dynamic-openapi-form-payload": """
        # Dynamic auth with form-urlencoded payload
        [auth.dynamic.openapi.BearerAuth]
        path = "/auth/token"
        payload = { grant_type = "password", username = "${TEST_STRING_1}", password = "${TEST_STRING_2}" }
        payload_content_type = "application/x-www-form-urlencoded"
        extract_selector = "/access_token"
    """,
    "common.auth-dynamic-openapi-payload": """
        # Valid dynamic auth with env-var payload
        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        payload = { username = "${TEST_STRING_1}", password = "${TEST_STRING_2}" }
        extract_selector = "/access_token"
    """,
    "common.auth-dynamic-openapi-retry-on-disabled": """
        # Dynamic auth with reactive refresh disabled
        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_selector = "/access_token"
        retry_on = []
    """,
    "common.auth-dynamic-openapi-retry-on": """
        # Dynamic auth with explicit reactive-refresh statuses
        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_selector = "/access_token"
        retry_on = [401, 419]
    """,
    "common.auth-openapi-api-key": """
        # Valid API Key auth
        [auth.openapi.ApiKeyAuth]
        api_key = "${TEST_STRING_1}"
    """,
    "common.auth-openapi-basic": """
        # Valid HTTP Basic auth
        [auth.openapi.BasicAuth]
        username = "${TEST_STRING_1}"
        password = "${TEST_STRING_2}"
    """,
    "common.auth-openapi-bearer": """
        # Valid HTTP Bearer token auth
        [auth.openapi.BearerAuth]
        bearer = "${TEST_STRING_1}"
    """,
    "common.generation-mode-negative": """
        [generation]
        mode = "negative"
    """,
    "common.incorrect-auth-basic-non-latin1": """
        [auth]
        basic = { "username" = "user", "password" = "Česko" }
    """,
    "common.incorrect-auth-basic": """
        [auth]
        basic = { "username" = "user" }  # Missing required "password" field
    """,
    "common.incorrect-auth-dynamic-openapi-bad-extract-from": """
        # extract_from must be "body", "header" or "cookie"
        [auth.dynamic.openapi.ApiKey]
        path = "/api/auth"
        extract_from = "query"
        extract_selector = "token"
    """,
    "common.incorrect-auth-dynamic-openapi-bad-method": """
        # method must be a valid HTTP method
        [auth.dynamic.openapi.BearerAuth]
        path = "/token"
        extract_selector = "/access_token"
        method = "FETCH"
    """,
    "common.incorrect-auth-dynamic-openapi-bad-path": """
        # path must start with /
        [auth.dynamic.openapi.BearerAuth]
        path = "api/auth"
        extract_selector = "/access_token"
    """,
    "common.incorrect-auth-dynamic-openapi-bad-selector": """
        # extract_selector for body extraction must be a JSON Pointer (start with /)
        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_from = "body"
        extract_selector = "access_token"
    """,
    "common.incorrect-auth-dynamic-openapi-basic-conflict": """
        # basic auth and dynamic openapi auth are mutually exclusive
        [auth]
        basic = { username = "user", password = "pass" }

        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_selector = "/access_token"
    """,
    "common.incorrect-auth-dynamic-openapi-conflict": """
        # Same scheme name in both static and dynamic auth
        [auth.openapi.BearerAuth]
        bearer = "static-token"

        [auth.dynamic.openapi.BearerAuth]
        path = "/api/auth"
        extract_selector = "/access_token"
    """,
    "common.incorrect-auth-mixed-basic-openapi": """
        # Cannot mix generic basic auth with OpenAPI-aware auth
        [auth.basic]
        username = "user"
        password = "pass"

        [auth.openapi.ApiKeyAuth]
        api_key = "${TEST_STRING_1}"
    """,
    "common.incorrect-auth-openapi-basic-missing-password": """
        # HTTP Basic auth missing password
        [auth.openapi.BasicAuth]
        username = "user"
    """,
    "common.incorrect-auth-openapi-empty-table": """
        [auth.openapi]
    """,
    "common.incorrect-auth-openapi-empty": """
        [auth.openapi.EmptyAuth]
    """,
    "common.incorrect-auth-openapi-mixed-api-key-basic": """
        # Mixed: has api_key and username but missing password
        [auth.openapi.Auth2]
        api_key = "key123"
        username = "user"
    """,
    "common.incorrect-auth-openapi-mixed-bearer-basic": """
        # Mixed: has bearer and password but missing username
        [auth.openapi.Auth3]
        bearer = "token123"
        password = "secret"
    """,
    "common.incorrect-auth-openapi-not-table": """
        [auth.openapi]
        ApiKeyAuth = "secret"
    """,
    "common.incorrect-auth-openapi-only-password": """
        # Has password field only, missing username (detects as Basic auth attempt)
        [auth.openapi.Auth1]
        password = "secret"
    """,
    "common.incorrect-auth-openapi-wrong-scheme": """
        # Invalid field - 'scheme'
        [auth.openapi.DigestAuth]
        scheme = "digest"
        username = "user"
        password = "pass"
    """,
    "common.incorrect-auth-openapi-wrong-type": """
        # Invalid property name - should be "api_key"
        [auth.openapi.ApiKeyAuth]
        value = "${TEST_STRING_1}"
    """,
    "common.incorrect-headers": """
        headers = ["X-API-Key", "API_KEY"]
    """,
    "common.incorrect-health-check-close-match": """
        suppress-health-check = ["data_too_lar"]
    """,
    "common.incorrect-health-check": """
        suppress-health-check = ["wrong value", "data_too_large"]
    """,
    "common.incorrect-origin-with-base-url": """
        base-url = "https://api.example.com/api"
        origin = "https://api.example.com"
    """,
    "common.incorrect-phase-coverage-unexpected-method": """
        # unexpected-methods entries must be valid HTTP methods
        [phases.coverage]
        unexpected-methods = ["FETCH"]
    """,
    "common.incorrect-placeholder": """
        base-url = "${TEST_STRING_1"
    """,
    "common.incorrect-project": """
        [[project]]
    """,
    "common.incorrect-rate-limit": """
        rate-limit = "wrong"
    """,
    "common.incorrect-request-retries-array": """
        request-retries = [3]
    """,
    "common.incorrect-request-retries-bool": """
        request-retries = true
    """,
    "common.incorrect-request-retries-empty-table": """
        request-retries = {}
    """,
    "common.incorrect-request-retries-negative": """
        request-retries = -1
    """,
    "common.incorrect-request-retries-string": """
        request-retries = "3"
    """,
    "common.incorrect-request-timeout-negative": """
        request-timeout = -1
    """,
    "common.incorrect-request-timeout": """
        request-timeout = "not a number"
    """,
    "common.incorrect-tls-verify": """
        tls-verify = [true, false]
    """,
    "common.incorrect-workers": """
        workers = "foo"
    """,
    "common.request-retries-integer": """
        request-retries = 3
    """,
    "common.request-retries-table": """
        request-retries = { max-attempts = 3 }
    """,
    "common.root": """
        color = true
        suppress-health-check = ["too_slow", "data_too_large"]
        max-failures = 42
        base-url = "https://api.example.com"
    """,
    "common.warnings-fail-on-all": """
        [warnings]
        fail-on = true
    """,
    "common.warnings-fail-on-specific": """
        [warnings]
        fail-on = ["validation_mismatch"]
    """,
    "common.warnings-object-complex": """
        [warnings]
        display = ["missing_auth", "validation_mismatch"]
        fail-on = ["validation_mismatch"]
    """,
    "common.warnings-object-with-display": """
        [warnings]
        display = ["missing_auth", "validation_mismatch"]
    """,
    "common.warnings-off": """
        warnings = false
    """,
    "common.warnings-on": """
        warnings = true
    """,
    "common.warnings-selection": """
        warnings = ["missing_auth"]
    """,
    "report.all_enabled": """
        [reports]
        directory = "reports"
        preserve-bytes = true

        [reports.junit]
        enabled = true
        path = "./test-reports/schemathesis-results.xml"

        [reports.vcr]
        enabled = true
        path = "cassettes/api"

        [reports.har]
        enabled = true
    """,
    "report.incorrect_section_type": """
        reports = 42
    """,
    "report.incorrect_types": """
        [reports]
        preserve-bytes = "yes"  # should be boolean
    """,
    "report.incorrect_types_in_vcr": """
        [reports.junit]
        enabled = 1  # should be boolean
    """,
    "report.invalid_format_names": """
        [reports]
        directory = "reports"

        [reports.junitxml]  # should be "junit"
        enabled = true

        [reports.csv]  # unsupported format
        enabled = true

        [reports.vcr]
        enabled = true
    """,
    "report.one_enabled": """
        [reports.har]
        enabled = true
    """,
    "report.unknown_keyword_vcr": """
        [reports.junit]
        foo = 1
    """,
    "report.unknown_keywords": """
        [reports]
        directory = "reports"
        # Unknown keyword
        output_format = "pretty"
        # Typo
        preserve_byte = false

        [reports.junit]
        enabled = true
    """,
    "report.with_env_var": """
        [reports]
        directory = "${TEST_STRING_1}"
    """,
    "report.with_env_var_in_path": """
        [reports.junit]
        enabled = true
        path = "${TEST_STRING_1}"
    """,
    "report.with_env_var_missing": """
        [reports]
        directory = "${TEST_STRING_NOT_PRESENT}"
    """,
    "cache.disabled": """
        [cache]
        enabled = false
    """,
    "cache.with_directory": """
        [cache]
        directory = "/var/cache/schemathesis"
    """,
    "parameters.basic": """
        [parameters]
        api_version = "v2"
    """,
    "parameters.mixed": """
        parameters = { user_id = 42 }

        [[operations]]
        include-name = "GET /users/{used_id}"
        parameters = { user_id = "${TEST_STRING_1}"}
    """,
    "parameters.with_locations": """
        parameters = { "path.user_id" = 42, "query.user_id" = 100 }
    """,
    "operations.basic": """
        [[operations]]
        include-name = "GET /users/{user_id}"
        parameters = { user_id = "${TEST_STRING_1}"}
    """,
    "operations.complex_many": """
        [[operations]]
        include-name-regex = ".+/users/.+"
        exclude-method = ["POST", "PUT"]
        parameters = { user_id = "${TEST_STRING_1}"}

        [[operations]]
        include-name = "GET /users/"
        exclude-by = "/x-prop == 42"
        parameters = { user_id = "${TEST_STRING_1}"}
    """,
    "operations.incorrect-exclude-tag-type": """
        [[operations]]
        exclude-tag = true
    """,
    "operations.incorrect-include-method-item-type": """
        [[operations]]
        include-method = ["GET", 1]
    """,
    "operations.incorrect-include-path-type": """
        [[operations]]
        include-path = 1
    """,
    "operations.missing_config_options": """
        [[operations]]
        # It does not change anything
        include-name = "GET /users/{user_id}"
    """,
    "operations.operation_openapi_auth_forbidden": """
        [[operations]]
        include-path = "/api"

        [operations.auth.openapi.ApiKeyHeader]
        api_key = "${TEST_STRING_1}"
    """,
    "operations.warnings-off": """
        [[operations]]
        include-name = "GET /users/{user_id}"
        warnings = false
    """,
    "operations.warnings-on": """
        [[operations]]
        include-name = "GET /users/{user_id}"
        warnings = true
    """,
    "fuzz.basic": """
        [fuzz]
        max-time = 3600
    """,
    "fuzz.incorrect-max-time": """
        [fuzz]
        max-time = "one hour"
    """,
    "dictionaries.basic": """
        [dictionaries.users]
        values = [42, 100, 9999]

        [dictionaries.edge]
        values = ["admin", "root", "' OR 1=1--"]
    """,
    "dictionaries.error_combining_values_and_from_file": """
        [dictionaries.x]
        values = [1]
        from-file = "x.dict"
    """,
    "dictionaries.error_empty_from_file": """
        [dictionaries.names]
        from-file = ""
    """,
    "dictionaries.error_empty_values": """
        [dictionaries.names]
        values = []
    """,
    "dictionaries.error_neither_values_nor_from_file": """
        [dictionaries.x]
    """,
    "dictionaries.error_parameter_unknown_dictionary": """
        [parameters]
        user_id = { dictionary = "missing" }
    """,
    "dictionaries.error_parameter_unknown_location_prefix": """
        [dictionaries.ids]
        values = [1]

        [parameters]
        "foo.bar" = { dictionary = "ids" }
    """,
    "dictionaries.error_type_wide_missing_probability": """
        [dictionaries.x]
        values = [1]

        [generation.dictionaries]
        integer = { dictionary = "x" }
    """,
    "dictionaries.error_type_wide_probability_above_one": """
        [dictionaries.x]
        values = [1]

        [generation.dictionaries]
        integer = { dictionary = "x", probability = 1.5 }
    """,
    "dictionaries.error_type_wide_probability_zero": """
        [dictionaries.x]
        values = [1]

        [generation.dictionaries]
        integer = { dictionary = "x", probability = 0 }
    """,
    "dictionaries.error_type_wide_unknown_dictionary": """
        [dictionaries.x]
        values = [1]

        [generation.dictionaries]
        integer = { dictionary = "missing", probability = 0.5 }
    """,
    "dictionaries.error_type_wide_zero_eligible": """
        [dictionaries.x]
        values = ["abc", "def"]

        [generation.dictionaries]
        integer = { dictionary = "x", probability = 0.5 }
    """,
    "dictionaries.error_unsupported_type_key": """
        [dictionaries.x]
        values = [1]

        [generation.dictionaries]
        boolean = { dictionary = "x", probability = 0.5 }
    """,
    "dictionaries.object_override_passthrough": """
        [parameters]
        "query.filter" = { kind = "active", limit = 10 }
    """,
    "dictionaries.parameter_specific_binding": """
        [dictionaries.ids]
        values = [1, 2, 3]

        [parameters]
        "path.user_id" = { dictionary = "ids" }
        "query.q" = { dictionary = "ids", probability = 0.25 }
    """,
    "dictionaries.scalar_and_binding_overrides": """
        [dictionaries.x]
        values = [1]

        [parameters]
        api_version = "v2"
        "query.q" = { dictionary = "x" }
    """,
    "dictionaries.type_wide_binding": """
        [dictionaries.edge]
        values = ["admin", "root"]

        [dictionaries.ids]
        values = [1, 2, 3]

        [generation.dictionaries]
        string = { dictionary = "edge", probability = 0.1 }
        integer = { dictionary = "ids", probability = 0.5 }
    """,
    "dictionaries.type_wide_eligibility": """
        [dictionaries.mixed]
        values = ["1", "2", "not-a-number"]

        [generation.dictionaries]
        integer = { dictionary = "mixed", probability = 0.5 }
    """,
}
WARNING_NAMES = [warning.value for warning in SchemathesisWarning]


@pytest.mark.parametrize("source", list(CONFIGS.values()), ids=list(CONFIGS))
def test_configs(monkeypatch, source, snapshot_config):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    monkeypatch.setenv("TEST_STRING_2", "bar")
    try:
        assert SchemathesisConfig.from_str(source) == snapshot_config
    except ConfigError as exc:
        assert str(exc) == snapshot_config
    except HookError as exc:
        assert str(exc) == snapshot_config


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$RdescudvJCsgt3ub",
            "$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$RdescudvJCsgt3ub",
        ),
        ("$TEST_STRING_1", "$TEST_STRING_1"),
        ("a$$b", "a$$b"),
        ("5 $", "5 $"),
        ("$${TEST_STRING_1}", "${TEST_STRING_1}"),
        ("${TEST_STRING_1}:$${TEST_STRING_1}", "foo:${TEST_STRING_1}"),
    ],
    ids=["password-hash", "bare-name", "double-dollar", "trailing-dollar", "escaped-placeholder", "mixed"],
)
def test_env_substitution_only_replaces_braced_placeholders(monkeypatch, value, expected):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    config = SchemathesisConfig.from_dict({"parameters": {"body.password": value}})
    assert config.projects.default.parameters == {"body.password": expected}


def test_escaped_placeholder_is_resolved_once(monkeypatch):
    monkeypatch.delenv("TEST_STRING_1", raising=False)
    config = SchemathesisConfig.from_str("[auth.wfc]\npath = '$${TEST_STRING_1}.yaml'")
    assert repr(config.projects.default.auth.wfc) == "WFCAuthConfig(path='${TEST_STRING_1}.yaml')"


def test_wfc_path_override_preserves_configured_values():
    config = SchemathesisConfig.from_dict(
        {
            "auth": {
                "wfc": {
                    "path": "configured.json",
                    "user": "alice",
                    "refresh_interval": 60,
                    "peers": ["alice", "bob"],
                }
            }
        }
    )

    config.projects.override.update(wfc_auth="override.json")

    assert config.projects.get_default().auth.wfc == WFCAuthConfig(
        path="override.json", user="alice", refresh_interval=60, peers=["alice", "bob"]
    )


def test_config_repr_omits_parameters_set_to_none():
    config = SchemathesisConfig.from_dict({"parameters": {"id": None, "b": 1}})
    assert repr(config.projects.default) == "ProjectConfig(parameters={'b': 1})"


def test_warnings_for_without_operations():
    config = SchemathesisConfig.from_dict({"warnings": False})
    assert config.projects.default.warnings_for(operation=None).display == []


def test_unexpected_methods_accept_every_default_method():
    methods = sorted(method.upper() for method in DEFAULT_UNEXPECTED_METHODS)
    assert (
        SchemathesisConfig.from_dict(
            {"phases": {"coverage": {"unexpected-methods": methods}}}
        ).projects.default.phases.coverage.unexpected_methods
        == DEFAULT_UNEXPECTED_METHODS
    )


def test_project_key_config_sync():
    ignored_in_operations_config = {
        "operations",
        "hooks",
        "workers",
        "base_url",
        "origin",
        "fuzz",
        "analysis",
        "baseline",
    }
    for key in ProjectConfig.__slots__:
        if key.startswith("_"):
            continue
        property_name = key.replace("_", "-")
        assert property_name in CONFIG_SCHEMA["$defs"]["ProjectConfig"]["properties"]
        assert property_name in CONFIG_SCHEMA["properties"]
        if key not in ignored_in_operations_config:
            assert property_name in CONFIG_SCHEMA["$defs"]["OperationConfig"]["properties"]
            assert key in OperationConfig.__slots__
    for key in ignored_in_operations_config:
        property_name = key.replace("_", "-")
        assert property_name not in CONFIG_SCHEMA["$defs"]["OperationConfig"]["properties"]
        assert key not in OperationConfig.__slots__


@pytest.mark.parametrize(
    ("project_analysis", "expected"),
    [
        ("[project.analysis.constants]\nenabled = true", True),
        ("", False),
    ],
    ids=["explicitly-enabled", "inherited"],
)
def test_project_constants_analysis_precedence(project_analysis, expected):
    config = SchemathesisConfig.from_str(
        f"""
        [analysis.constants]
        enabled = false

        [[project]]
        title = "Enabled API"
        {project_analysis}
        """
    )

    project = config.projects.get({"info": {"title": "Enabled API"}})

    assert project.analysis.constants.enabled is expected


def test_override_can_reenable_constants_analysis():
    config = SchemathesisConfig.from_str("[analysis.constants]\nenabled = false\n")

    config.projects.override.analysis.constants.enabled = True

    assert config.projects.get_default().analysis.constants.enabled is True


def test_resolved_constants_config_does_not_alias_project_config():
    config = SchemathesisConfig.from_str(
        """
        [[project]]
        title = "Enabled API"
        [project.analysis.constants]
        enabled = true
        """
    )

    project = config.projects.get({"info": {"title": "Enabled API"}})
    project.analysis.constants.enabled = False

    assert config.projects.named["Enabled API"].analysis.constants.enabled is True


def test_config_path_from_path(tmp_path):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    config = SchemathesisConfig.from_path(config_file)

    assert config.config_path == str(config_file.resolve())


def test_config_path_from_discover(tmp_path, monkeypatch):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    monkeypatch.chdir(tmp_path)
    config = SchemathesisConfig.discover()

    assert config.config_path == str(config_file.resolve())


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SchemathesisConfig(),
        lambda: SchemathesisConfig.from_dict({"color": True}),
    ],
    ids=["default", "from_dict"],
)
def test_config_path_none_when_no_file(factory):
    config = factory()
    assert config.config_path is None


def test_project_config_path_delegates_to_parent(tmp_path):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("color = true\n")

    config = SchemathesisConfig.from_path(config_file)
    project_config = config.projects.default

    assert project_config.config_path == str(config_file.resolve())


def test_project_config_path_none_without_parent():
    project_config = ProjectConfig()
    assert project_config.config_path is None


def test_filter_set_with_returns_copy_when_no_operations():
    # See GH-3572.
    # filter_set_with() must return a copy when there are no `[[operations]]` configured.
    # This avoids mutations that can corrupt the execution flow on subsequent runs
    ops = OperationsConfig()
    original = FilterSet()
    original.exclude(path="/admin")

    assert ops.filter_set_with(include=original) is not original


@pytest.mark.parametrize(
    ("template", "select"),
    [
        ('warnings = ["{name}"]', lambda warnings: warnings.display),
        ('[warnings]\ndisplay = ["{name}"]', lambda warnings: warnings.display),
        ('[warnings]\nfail-on = ["{name}"]', lambda warnings: warnings.fail_on),
    ],
    ids=["shorthand", "display", "fail-on"],
)
@pytest.mark.parametrize("name", WARNING_NAMES, ids=WARNING_NAMES)
def test_every_warning_name_is_usable_in_config(name, template, select):
    config = SchemathesisConfig.from_str(template.format(name=name))

    assert select(config.projects.default.warnings_for(operation=None)) == [SchemathesisWarning.from_str(name)]


def test_warning_names_match_config_schema():
    assert CONFIG_SCHEMA["$defs"]["WarningName"]["enum"] == WARNING_NAMES


@pytest.mark.parametrize(
    ("source", "section", "description"),
    [
        ('warnings = ["mising_auth"]', "root", "Item #0 in the 'warnings' array"),
        ('[warnings]\ndisplay = ["mising_auth"]', "[warnings]", "Item #0 in the 'display' array"),
        ('[warnings]\nfail-on = ["mising_auth"]', "[warnings]", "Item #0 in the 'fail-on' array"),
    ],
    ids=["shorthand", "display", "fail-on"],
)
def test_unknown_warning_name_is_reported_with_a_suggestion(source, section, description):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(source)

    assert str(exc.value) == (
        f"Error in {section} section:\n  Invalid value:\n\n"
        f"  - {description} -> 'mising_auth' is not a valid value. Did you mean 'missing_auth'?\n\n"
        f"Valid values are: {', '.join(repr(name) for name in sorted(WARNING_NAMES))}."
    )


@pytest.mark.parametrize(
    ("method", "suggestion"),
    [("FETCH", " Did you mean 'PATCH'?"), ("zzzzzzzz", "")],
    ids=["close-match", "no-match"],
)
def test_unexpected_method_error_suggests_only_a_close_match(method, suggestion):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(f'[phases.coverage]\nunexpected-methods = ["{method}"]')

    assert str(exc.value) == (
        "Error in [phases.coverage] section:\n  Invalid value:\n\n"
        f"  - Item #0 in the 'unexpected-methods' array -> '{method}' is not a valid value.{suggestion}\n\n"
        "Valid values are: 'DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT', 'QUERY', 'TRACE'."
    )


def test_generation_mode_all_and_maximize_list():
    generation = SchemathesisConfig.from_str(
        '[generation]\nmode = "all"\nmaximize = ["response_time"]\n'
    ).projects.default.generation

    assert (generation.modes, [metric.__name__ for metric in generation.maximize]) == (
        list(schemathesis.GenerationMode),
        ["response_time"],
    )


def test_coverage_generation_rejects_max_examples():
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str("[phases.coverage.generation]\nmax-examples = 5\n")

    assert str(exc.value) == (
        "`max-examples` has no effect in the coverage phase: it generates every case the schema defines."
    )


def test_max_examples_loads_for_bounded_generation():
    assert (
        SchemathesisConfig.from_str("[generation]\nmax-examples = 5\n").projects.default.generation.max_examples,
        SchemathesisConfig.from_str(
            "[phases.fuzzing.generation]\nmax-examples = 5\n"
        ).projects.default.phases.fuzzing.generation.max_examples,
    ) == (5, 5)


def test_warnings_disabled_in_table_form_hide_and_never_fail():
    warnings = SchemathesisConfig.from_str("[warnings]\nenabled = false\nfail-on = true\n").projects.default.warnings

    assert (warnings.display, warnings.fail_on) == ([], [])


def test_workers_auto_uses_available_cpus():
    config = SchemathesisConfig.from_str('workers = "auto"')
    override = config.projects.override
    override.update(workers="auto")

    assert (config.projects.default.workers, override.workers) == (get_workers_count(), get_workers_count())


@pytest.mark.parametrize("workers", [0, -1])
def test_workers_below_one(workers):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(f"workers = {workers}")
    assert str(exc.value) == (
        f"Invalid value for 'workers': {workers}\n\n"
        "Expected either:\n"
        "  - A positive integer (e.g., workers = 4)\n"
        '  - The string "auto" for automatic detection (workers = "auto")'
    )


def test_project_workers_below_one():
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str('[[project]]\ntitle = "a"\nworkers = 0')
    assert str(exc.value) == (
        "Error in [project.0] section:\n  Value out of range:\n\n  - 'workers' -> Must be at least 1, but got 0."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            '[[project]]\ntitle = "a"\nworkers = "many"\n',
            "Error in [project.0] section:\n  Type error:\n\n"
            "  - 'workers' -> Must be an integer or 'auto', but got string: 'many'",
        ),
        (
            'generation.maximize = "foo"\n',
            "Error in [generation] section:\n  Type error:\n\n"
            "  - 'maximize' -> Must be 'response_time' or an array, but got string: 'foo'",
        ),
    ],
    ids=["project-workers-const", "maximize-enum"],
)
def test_any_of_with_literal_alternative_lists_allowed_values(source, expected):
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_str(source)
    assert str(exc.value) == expected


def test_standalone_project_config_reads_discovered_config_file(tmp_path, monkeypatch):
    (tmp_path / "schemathesis.toml").write_text("seed = 42\n")
    monkeypatch.chdir(tmp_path)

    assert ProjectConfig().seed == 42


def test_run_limits_set_on_project_apply_to_whole_config():
    config = SchemathesisConfig()
    project = config.projects.default

    project.max_failures = 3
    project.max_time = 60
    project.seed = 7

    assert (config.max_failures, config.max_time, config.seed) == (3, 60, 7)


def test_custom_check_disabled_in_config():
    checks = SchemathesisConfig.from_str("[checks.custom_check]\nenabled = false\n").projects.default.checks

    assert repr(checks.get_by_name(name="custom_check")) == "SimpleCheckConfig(enabled=False)"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("[checks]\nenabled = false\n", False),
        ("[checks]\nenabled = false\n[checks.custom_check]\nthreshold = 1\n", False),
        ("[checks]\nenabled = false\n[checks.custom_check]\nenabled = true\n", True),
        ("[checks]\nenabled = false\n[phases.fuzzing.generation]\nmax-examples = 5\n", False),
    ],
    ids=["unconfigured", "kwargs-only", "re-enabled", "phase-merged"],
)
def test_global_checks_enabled_applies_to_custom_checks(source, expected):
    project = SchemathesisConfig.from_str(source).projects.default

    assert project.checks_config_for(phase="fuzzing").get_by_name(name="custom_check").enabled is expected


@pytest.mark.parametrize(
    ("override", "project", "expected"),
    [
        ({"excluded_check_names": ["not_a_server_error"]}, {"included_check_names": ["not_a_server_error"]}, False),
        ({"included_check_names": ["not_a_server_error"]}, {"excluded_check_names": ["not_a_server_error"]}, True),
    ],
    ids=["override-excludes", "override-includes"],
)
def test_override_check_selection_beats_project_selection(override, project, expected):
    config = SchemathesisConfig()
    config.projects.override.checks.update(**override)
    config.projects.default.checks.update(**project)

    assert config.projects.get_default().checks.not_a_server_error.enabled is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Bearer ${API_TOKEN}", "Missing environment variable: `API_TOKEN`"),
        (
            "${API_USER}:${API_TOKEN}@${API_USER}",
            "Missing environment variables: `API_USER`, `API_TOKEN`",
        ),
    ],
    ids=["single", "several"],
)
def test_missing_env_var_error_names_the_variable(monkeypatch, value, expected):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("API_USER", raising=False)
    with pytest.raises(ConfigError) as exc:
        SchemathesisConfig.from_dict({"headers": {"Authorization": value}})
    assert str(exc.value) == expected
