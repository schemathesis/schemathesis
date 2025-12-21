import pytest


@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_proxy_is_used(testdir, is_lazy, openapi3_base_url):
    proxy_url = "http://127.0.0.1:8080"
    if is_lazy:
        schema_setup = f"""
@pytest.fixture
def api_schema():
    config = SchemathesisConfig()
    config.projects.default.update(proxy="{proxy_url}", base_url="{openapi3_base_url}")
    return schemathesis.openapi.from_dict(raw_schema, config=config)

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    else:
        schema_setup = f"""
config = SchemathesisConfig()
config.projects.default.update(proxy="{proxy_url}", base_url="{openapi3_base_url}")
schema = schemathesis.openapi.from_dict(raw_schema, config=config)
"""

    testdir.make_test(
        f"""
{schema_setup}

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_proxy(case):
    with pytest.raises(requests.exceptions.ProxyError):
        case.call()
"""
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_request_timeout_is_used(testdir, is_lazy, openapi3_base_url):
    timeout = 0.01
    if is_lazy:
        schema_setup = f"""
@pytest.fixture
def api_schema():
    config = SchemathesisConfig()
    config.projects.default.update(request_timeout={timeout}, base_url="{openapi3_base_url}")
    return schemathesis.openapi.from_dict(raw_schema, config=config)

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    else:
        schema_setup = f"""
config = SchemathesisConfig()
config.projects.default.update(request_timeout={timeout}, base_url="{openapi3_base_url}")
schema = schemathesis.openapi.from_dict(raw_schema, config=config)
"""

    testdir.make_test(
        f"""
{schema_setup}

@schema.include(path_regex="slow").parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_timeout(case):
    with pytest.raises(requests.exceptions.Timeout):
        case.call()
""",
        paths={"/slow": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_wait_for_schema_is_used(mocker):
    import schemathesis

    # Mock requests.get to track wait_for_schema parameter
    mock_load_from_url = mocker.patch("schemathesis.openapi.loaders.load_from_url")
    mock_response = mocker.Mock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.text = '{"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0.0"}, "paths": {}}'
    mock_load_from_url.return_value = mock_response

    wait_for_schema_value = 42.5
    config = schemathesis.config.SchemathesisConfig(wait_for_schema=wait_for_schema_value)

    # Call from_url without explicit wait_for_schema - should use config
    schemathesis.openapi.from_url("http://example.com/openapi.json", config=config)

    # Verify wait_for_schema from config was passed to load_from_url
    assert mock_load_from_url.called
    call_kwargs = mock_load_from_url.call_args[1]
    assert call_kwargs.get("wait_for_schema") == wait_for_schema_value


@pytest.mark.parametrize("is_lazy", [False, True])
def test_fuzzing_phase_max_examples_is_used(testdir, is_lazy):
    max_examples = 3
    if is_lazy:
        schema_setup = f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.phases.fuzzing.generation.update(max_examples={max_examples})
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    return schema

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    else:
        schema_setup = f"""
schema = schemathesis.openapi.from_dict(raw_schema)
schema.config.phases.fuzzing.generation.update(max_examples={max_examples})
schema.config.phases.examples.enabled = False
schema.config.phases.coverage.enabled = False
schema.config.phases.stateful.enabled = False
"""

    testdir.make_test(
        f"""
{schema_setup}

@schema.include(path_regex="test").parametrize()
def test_max_examples(request, case):
    request.config.HYPOTHESIS_CASES += 1
    pass
""",
        paths={
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 0, "maximum": 1000},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([rf"Hypothesis calls: {max_examples}"])
