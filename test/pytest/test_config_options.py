import pytest


@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_seed_is_used(testdir, is_lazy, mocker):
    from schemathesis.generation.hypothesis import builder

    spy = mocker.spy(builder, "create_test")
    seed = 42

    if is_lazy:
        schema_setup = f"""
@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict(raw_schema, config=SchemathesisConfig(seed={seed}))

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    else:
        schema_setup = f"schema = schemathesis.openapi.from_dict(raw_schema, config=SchemathesisConfig(seed={seed}))"

    testdir.make_test(
        f"""
{schema_setup}

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_seed(case):
    pass
"""
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)
    assert spy.call_args.kwargs["config"].seed == seed


@pytest.mark.parametrize("is_lazy", [False, True])
def test_explicit_seed_not_overridden(testdir, is_lazy, mocker):
    from schemathesis.generation.hypothesis import builder

    spy = mocker.spy(builder, "create_test")

    if is_lazy:
        schema_setup = """
@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict(raw_schema, config=SchemathesisConfig(seed=999))

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    else:
        schema_setup = "schema = schemathesis.openapi.from_dict(raw_schema, config=SchemathesisConfig(seed=999))"

    testdir.make_test(
        f"""
{schema_setup}

@schema.parametrize()
@seed(42)
@settings(max_examples=1, phases=[Phase.generate])
def test_seed(case):
    pass
"""
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)

    # Verify the test_func passed to create_test already has the explicit seed
    test_func = spy.call_args.kwargs["test_func"]
    assert hasattr(test_func, "_hypothesis_internal_use_seed")
    assert test_func._hypothesis_internal_use_seed == 42


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
